<!-- The next release's notes; see docs/development.md#release-notes. -->

### Added

- **Project:** a game takes a **Rename** on its page, so games opened from a folder called "translation" are told apart; its folder keeps its name.
- **Translate:** an Assistant-led project can have its assistant only extract images, leaving investigating and translating them to you in the Images tab.
- **Settings > Remote access:** drive this install from another computer's browser, with a token and its own address and port.
- **Translate:** Assistant-led knows the SAS5 engine, with tools for its .sec5 text, .exe UI resources and .iar images, and keeps ASCII tildes.
- **Checkpoints:** a game's `.dazedtl` work, such as Plugin files and edited images, gets its own Git history in `.dazedtl/work.git`.

### Changed

- **Translate:** an address term stays as it is used, "Onii-chan" as a pet name and "my older brother" as a relationship.
- **Translate:** every character is treated as fictional and of no age, and no line is softened over one.

### Fixed

- **Translate:** an OpenRouter Batch on a Google model no longer fails every request with "Invalid response_json_schema".
- **Translate:** a Batch run no longer stops with "status failed (HTTP 404)" over a batch OpenRouter had just accepted.
- **Translate:** a Batch run no longer fails with its sent requests unread when the glossary or guidance is edited while it waits.
- **Translate:** an Assistant-led run no longer fails a whole request over one line that broke a code, and says how many lines it kept untranslated.
- **Translate:** accepting a batch now rejects speaker-name echoes and one-line shifts instead of passing them.
- **Translate:** stale-file refusals name the changed files and point corrections on an older run to the latest organized run.
- **Text QA:** ordinary Japanese-to-English lines are no longer flagged for an unusual length, nor "Scene 1" as a number mismatch.
- **Release:** the playtest plays common-event stops, skips battles a game ships no enemy art for, and accepts a censor mask Text QA keeps.
- **Release:** the playtest's fixes reach the game when nothing new breaks, even beside a problem no fix removes.
- **Release:** the playtest no longer lists MZ's splash screen as a plugin screen, nor blames Game End on a title started from pictures.
- **Release:** a game whose text is all in its images can be packaged.
- **Release:** the assistant's patch no longer leaves out a file it translated without saying so.
- **Images:** an image applied to a decoded folder no longer reads as in the game, nor as changed when its report is read again.
- **Images:** an image task names each variant family once, so copying a big game's investigation task no longer fails as too large.
- **Release:** the assistant's patch waits for a GameUpdate repository instead of shipping the updater without one, or builds once GameUpdate is off.
