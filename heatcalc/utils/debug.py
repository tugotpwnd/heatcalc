import json
from pprint import pformat

def debug_meta(project, label: str):
    print("\n" + "=" * 80)
    print(f"[META DEBUG] {label}")
    print("- ProjectMeta:")
    print(pformat(project.meta.__dict__, width=120))
    print("- Louvre definition:")
    print(pformat(project.meta.louvre_definition, width=120))
    print("=" * 80 + "\n")
