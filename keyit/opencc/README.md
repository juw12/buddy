# OpenCC dictionaries

Character and phrase tables for Simplified <-> Traditional Chinese, used by `keyit/keyit_script.py`.

- Source: [BYVoid/OpenCC](https://github.com/BYVoid/OpenCC), tag `ver.1.1.9`, folder `data/dictionary/`
- Licence: Apache License 2.0, in `LICENSE` beside this file (also listed in `THIRD_PARTY_NOTICES.md` at the top of this repository)
- Files, unchanged: `STCharacters.txt`, `STPhrases.txt`, `TWVariants.txt`

Format: one entry per line, `source<TAB>target [target2 ...]`; the first target is the default.
Buddy reads only these three files. Traditional -> Simplified is the same tables read backwards.
