"""Rebuild keyit/names/names.txt: the first and last names "My words" must never rewrite.

The names come from the person providers of Faker (MIT License), every locale, Latin script only, with
accents removed and folded to lower case: "Zaira", "Siti", "Nguyễn" -> zaira, siti, nguyen. Faker is only
needed to run this script; Buddy itself reads the text file.

    python -m pip install faker==40.52.0 --target <folder>
    python tools/make_names.py --faker <folder>
"""
import argparse
import importlib
import pkgutil
import re
import sys
import unicodedata
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "keyit" / "names" / "names.txt"
LATIN = re.compile(r"^[A-Za-z\u00C0-\u024F\u1E00-\u1EFF' -]+$")
LISTS = re.compile(r"^(first|last)_names?(_\w+)?$|^(first|last)_romanized_names$")


def plain(text):
    folded = unicodedata.normalize("NFKD", text)
    return "".join(char for char in folded if not unicodedata.combining(char)).lower().strip("'")


def collect():
    import faker.providers.person as person
    names = set()
    for info in pkgutil.iter_modules(person.__path__):
        try:
            module = importlib.import_module("faker.providers.person." + info.name)
        except Exception:
            continue
        provider = getattr(module, "Provider", None)
        for attribute in dir(provider) if provider else []:
            if not LISTS.match(attribute):
                continue
            value = getattr(provider, attribute)
            if isinstance(value, dict):
                value = list(value)
            if not isinstance(value, (list, tuple)):
                continue
            for item in value:
                if not isinstance(item, str) or not LATIN.match(item):
                    continue
                for part in re.split(r"[\s-]+", item):
                    name = plain(part)
                    if len(name) >= 3 and name.isascii() and name.isalpha():
                        names.add(name)
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--faker", help="folder Faker is installed in (default: the current environment)")
    args = parser.parse_args()
    if args.faker:
        sys.path.insert(0, args.faker)
    import faker
    names = collect()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(name + "\n" for name in sorted(names)), encoding="utf-8", newline="\n")
    print(f"{len(names)} names from Faker {faker.VERSION} -> {OUT}")


if __name__ == "__main__":
    main()
