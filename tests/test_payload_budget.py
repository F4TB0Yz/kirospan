"""Tests de la guarda de tamaño del payload (`domain.payload_budget`).

El límite real del runtime de Kiro se midió empíricamente y no es sobre bytes del
body: es contenido + ~930 unidades por mensaje del historial, con un presupuesto
de ~1.066.700. Un body de 1.0 MB con 401 mensajes lo revienta mientras uno de
1.07 MB con un solo mensaje pasa.
"""

import json

import pytest

from gateway.domain.errors import InvalidRequest
from gateway.domain.payload_budget import (
    DEFAULT_MAX_PAYLOAD_UNITS,
    FIXED_OVERHEAD_PER_MESSAGE,
    enforce_payload_budget,
    payload_units,
)


def _state(history_turns: int, turn_chars: int = 200, current: str = "ahora") -> dict:
    history = []
    for index in range(history_turns):
        history.append({"userInputMessage": {"content": f"u{index}" + "x" * turn_chars}})
        history.append({"assistantResponseMessage": {"content": f"a{index}" + "y" * turn_chars}})
    state = {"conversationState": {"currentMessage": {"userInputMessage": {"content": current}}}}
    if history:
        state["conversationState"]["history"] = history
    return state


def test_small_payload_is_untouched():
    state = _state(2)
    snapshot = json.dumps(state, sort_keys=True)

    dropped = enforce_payload_budget(state)

    assert dropped == 0
    assert json.dumps(state, sort_keys=True) == snapshot


def test_units_charge_content_plus_per_message_overhead():
    state = _state(0, current="abcd")

    # contenido + overhead del mensaje + el JSON de `tools` (lista vacía: "[]")
    expected = len("abcd") + FIXED_OVERHEAD_PER_MESSAGE + len("[]")
    assert payload_units(state) == expected


def test_units_grow_with_message_count_even_at_equal_content():
    # Mismo contenido total, distinto número de mensajes: el de más mensajes
    # pesa más, que es exactamente lo que Kiro cobra.
    few = _state(2, turn_chars=50)
    many = _state(40, turn_chars=1)

    assert payload_units(many) > payload_units(few)


def test_oversized_history_is_trimmed_until_it_fits():
    state = _state(4000)

    dropped = enforce_payload_budget(state)

    assert dropped > 0
    assert payload_units(state) <= DEFAULT_MAX_PAYLOAD_UNITS
    assert state["conversationState"]["currentMessage"]["userInputMessage"]["content"] == "ahora"


def test_trimming_keeps_the_newest_history_turns():
    state = _state(4000)
    newest_before = state["conversationState"]["history"][-1]["assistantResponseMessage"]["content"]

    enforce_payload_budget(state)

    newest_after = state["conversationState"]["history"][-1]["assistantResponseMessage"]["content"]
    assert newest_after == newest_before


def test_history_key_is_removed_when_everything_is_dropped():
    # Historial diminuto y un turno actual que llena el presupuesto casi por
    # completo: cualquier entrada de historial lo desborda, así que se va todo.
    state = _state(3, turn_chars=10, current="z" * (DEFAULT_MAX_PAYLOAD_UNITS - 1100))

    dropped = enforce_payload_budget(state)

    assert dropped == 6
    assert "history" not in state["conversationState"]
    assert payload_units(state) <= DEFAULT_MAX_PAYLOAD_UNITS


def test_irreducible_payload_raises_legible_error():
    state = _state(0, current="z" * (DEFAULT_MAX_PAYLOAD_UNITS + 1000))

    with pytest.raises(InvalidRequest) as excinfo:
        enforce_payload_budget(state, model_id="glm-5")

    message = str(excinfo.value)
    assert "1 MB" in message
    assert "glm-5" in message


def test_custom_budget_is_honored():
    state = _state(20)

    dropped = enforce_payload_budget(state, max_units=5000)

    assert dropped > 0
    assert payload_units(state) <= 5000
