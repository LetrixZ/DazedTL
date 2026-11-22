<!-- The next release's notes; see docs/development.md#release-notes. -->

### Added

- **Translate:** an Assistant-led project can have its assistant only extract images, leaving investigating and translating them to you in the Images tab.
- **Settings > Remote access:** drive this install from another computer's browser, with a token and its own address and port.

### Changed

- **Translate:** an address term stays as it is used, "Onii-chan" as a pet name and "my older brother" as a relationship.
- **Translate:** every character is treated as fictional and of no age, and no line is softened over one.

### Fixed

- **Translate:** an OpenRouter Batch on a Google model no longer fails every request with "Invalid response_json_schema".
- **Translate:** a Batch run no longer stops with "status failed (HTTP 404)" over a batch OpenRouter had just accepted.
