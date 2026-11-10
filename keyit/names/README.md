# Names

First and last names that "My words" never rewrites, used by `keyit/keyit_words.py`. Without them a real
name that sounds like one of your words would be replaced: "Zaira's answer" sounds like "ZIRIS answer".

- Source: the person providers of [Faker](https://github.com/joke2k/faker) 40.52.0, every locale
- Licence: MIT License, in `LICENSE` beside this file (also listed in `THIRD_PARTY_NOTICES.md` at the top of this repository)
- File: `names.txt`, one name per line, lower case, accents removed, Latin script only, at least 3 letters
- Rebuild: `python tools/make_names.py --faker <folder with Faker installed>`

A name only stops a replacement. If a colleague's name is missing, add it to "My words" as it is written.
