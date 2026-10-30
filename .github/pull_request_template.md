Closes #

## What and why


## Verification
<!-- State plainly what was and was not verified. -->
- [ ] `./scripts/validate.sh` passed
- [ ] `./scripts/verify.sh --full` passed (or: not run, because ...)
- [ ] Not verified: 

## Checklist
- [ ] Branched from `dev`; this PR targets `dev`
- [ ] `docs/progress.yaml` updated if what is built changed
- [ ] Related docs updated (manual, guides, ADR, SPEC, getting-started)
- [ ] Wiki synced (`python scripts/sync_wiki.py --push`) and in-page help checked
- [ ] Easy to follow: a new user can do it from the docs alone
- [ ] Tested against the monitoring client (state what was and was not exercised)
- [ ] No personal data, tokens or local paths in the diff or this text (`./scripts/check-privacy.sh --all`)
