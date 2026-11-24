"""Identidad estable de sesión para emular el ciclo de vida del CLI de Kiro.

kiro-cli genera un conversationId y un session ID al iniciar el proceso y los
reutiliza durante toda la vida del CLI. El gateway anterior generaba un UUID
nuevo por request (conversationId nuevo siempre), lo que delataba automatización
ante AWS: miles de conversaciones de un solo mensaje desde la misma cuenta.

Estas constantes son la única fuente de verdad para session/conversation IDs:
tanto el builder de conversación (conversation.py) como el cliente de runtime
(kiro_runtime.py) las importan, quedan sincronizados sin pasarse parámetros.
"""

import uuid

SESSION_ID = str(uuid.uuid4())
CONVERSATION_ID = str(uuid.uuid4())

# User-Agent de cliente de escritorio real. kiro-cli manda un UA de escritorio
# (tipo Kiro/... Safari/...). Antes el gateway no lo seteaba y httpx enviaba
# "python-httpx/x.y.z", lo que delataba automatización ante AWS.
KIRO_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Kiro/0.2.0 Safari/537.36"
)
