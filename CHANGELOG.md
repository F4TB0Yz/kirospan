# Changelog

All notable public changes to KiroSpan are documented here.

## [1.1.0](https://github.com/F4TB0Yz/kirospan/compare/kirospan-v1.0.0...kirospan-v1.1.0) (2026-08-02)


### Features

* add global Hermes integration ([cbe3b03](https://github.com/F4TB0Yz/kirospan/commit/cbe3b030876a59ad66811ed78aeb9bc7763eb5d0))
* add global Hermes integration ([af7090b](https://github.com/F4TB0Yz/kirospan/commit/af7090b4d72e51b0d6bd70ce2b8391ebad1117ca))
* add Hermes profile integration ([d45f7bc](https://github.com/F4TB0Yz/kirospan/commit/d45f7bc469fe7d8a2308468d278b60912531e44a))
* add reasoning stream support ([c854bd5](https://github.com/F4TB0Yz/kirospan/commit/c854bd53059468042b349eb3ed40f272819cae34))
* add reasoning stream support ([72b78d1](https://github.com/F4TB0Yz/kirospan/commit/72b78d156672927b0d2d533fe494eab617889637))
* initial public release ([b21e314](https://github.com/F4TB0Yz/kirospan/commit/b21e3141349088beb8370f5ddf4dfb662a6abbad))


### Bug Fixes

* corregir manejo de errores y persistencia del gateway ([ff73b1a](https://github.com/F4TB0Yz/kirospan/commit/ff73b1ab5e3f74569c3e33492ee0d28e4f744762))
* insertar token cuando falta la fila sqlite ([f60b3a0](https://github.com/F4TB0Yz/kirospan/commit/f60b3a09cbdee87846f6f87d4d2b390bfdc983ef))
* propagar errores upstream en respuestas no-stream ([bc0e0bd](https://github.com/F4TB0Yz/kirospan/commit/bc0e0bd796b6fba321e70ddeee7c99106ac8190e))
* rechazar eventstreams truncados ([2bfdd54](https://github.com/F4TB0Yz/kirospan/commit/2bfdd54cdc3f14fb7e92d34923b83960d3b132e9))
* responder 401 ante credenciales Kiro inválidas ([34e8b6d](https://github.com/F4TB0Yz/kirospan/commit/34e8b6debd8b612527892548cd0a04ccc4c11ef7))
* retry transient Kiro stream exceptions safely ([75d9813](https://github.com/F4TB0Yz/kirospan/commit/75d9813bec73e45e07c865b414fcb08f20e73c06))


### Documentation

* mejorar guías de uso y diagnóstico ([6531861](https://github.com/F4TB0Yz/kirospan/commit/6531861bcd06961cf7c813055d5d8cffd7676153))
* mejorar guías de uso y diagnóstico ([17e5c6c](https://github.com/F4TB0Yz/kirospan/commit/17e5c6c92b24345870a4b17b2c8b5b79097e3263))

## [1.0.0] - 2026-08-01

First stable public release.

### Added

- OpenAI-compatible chat completions and model discovery.
- Anthropic-compatible messages and token counting.
- Streaming text, tool calls, images, and reasoning output.
- Local gateway authentication with secure loopback defaults.
- Kiro CLI credential loading, refresh, and safe persistence behavior.
- Hermes Agent installer for macOS and client configuration guides.
- Python 3.11 through 3.14 CI, Dependabot, GitGuardian, and GitHub secret
  scanning protections.

### Security

- Upstream response bodies and stream exception details are excluded from
  public errors and logs.
- Public-use warnings, responsible-use guidance, vulnerability reporting, and
  contribution templates are included.

### Platform support

- macOS is supported.
- Linux support is experimental.
- Windows is not supported in this release.
