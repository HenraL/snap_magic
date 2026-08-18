#!/usr/bin/env python3
"""
prepare_kicad.py — normalize SnapMagic component downloads for KiCad 9/10 import.

Pure stdlib, no venv required. Run from anywhere; the repo root is derived from
this file's location (safe to use when snap_magic is a git submodule).

Usage:
    ./bonus/prepare_kicad.py                        # normalize + validate every component
    ./bonus/prepare_kicad.py <component-dir> [...]  # only the given component folder(s)
    ./bonus/prepare_kicad.py --check                # validate only, make no changes
    ./bonus/prepare_kicad.py --check <component-dir> [...]

For each component folder this ensures a uniform, KiCad-importable layout:

    <component>/
    ├── <component>.kicad_sym          # symbol lib
    ├── <component>.pretty/
    │   └── <footprint>.kicad_mod      # footprint internal name == file name
    ├── <component>.3dshape/
    │   └── <model>.step               # referenced as ../<component>.3dshape/<model>
    └── how-to-import.htm / link.md

Fixes applied:
  - footprints moved into <component>.pretty/ (dup root copies removed)
  - footprint internal name aligned to file name (KiCad skips mismatched ones)
  - 3D model block added when a step exists but is not referenced
  - model path rewritten to library-relative ../<shapes>/<model> form
  - loose root .step moved into the 3dshape folder when it has no folder home
    (content-identical duplicates are removed)
  - symbol "Footprint" property repointed to <component>:<footprint-name>
  - <component>.3dshape folder name normalized (was .3dshapes / mismatched)
  - trailing spaces in component folder names removed

Components without a 3D model are left model-less: reported as a note, never
treated as an error. The check mode exits 1 if any component fails.
"""
import os
import re
import sys
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"bonus", "tmp"}
MODEL_BLOCK = (
    '\n  (model "%(path)s"\n'
    '    (offset (xyz 0 0 0))\n'
    '    (scale (xyz 1 1 1))\n'
    '    (rotate (xyz 0 0 0))\n'
    '  )\n)'
)


def _log(notes, severity, comp, msg):
    notes.append((severity, comp, msg))


def find_shapes_dir(comp):
    for entry in sorted(os.listdir(comp)):
        full = os.path.join(comp, entry)
        if os.path.isdir(full) and (entry.lower().endswith(".3dshape")
                                    or entry.lower().endswith(".3dshapes")):
            return full
    return None


def find_model_file(shapes_dir):
    if shapes_dir is None:
        return None
    base = os.path.basename(os.path.dirname(shapes_dir))
    candidates = []
    for entry in os.listdir(shapes_dir):
        if entry.lower().endswith(".step"):
            if entry.lower() == base.lower() + ".step":
                return entry
            candidates.append(entry)
    return candidates[0] if candidates else None


def fix_footprint(mod_path, shapes_dir, model_file, notes, comp, check_only):
    with open(mod_path, encoding="utf-8") as fh:
        content = fh.read()
    changed = False
    fname = os.path.basename(mod_path)[: -len(".kicad_mod")]
    im = re.search(r"\(footprint\s+([^\s\(]+)", content)
    if im and im.group(1) != fname:
        _log(notes, "fix", comp, "%s: internal name %r -> %r"
             % (os.path.basename(mod_path), im.group(1), fname))
        if not check_only:
            content = content.replace("(footprint " + im.group(1),
                                      "(footprint " + fname, 1)
        changed = True

    if shapes_dir is not None and model_file is not None:
        rel = ("../" + os.path.basename(shapes_dir) + "/" + model_file).replace(os.sep, "/")
        existing = re.search(r'\(model\s+"[^"]*"', content)
        if existing and existing.group(0) != '(model "' + rel + '"':
            _log(notes, "fix", comp, "%s: model path -> %s"
                 % (os.path.basename(mod_path), rel))
            if not check_only:
                content = content.replace(existing.group(0), '(model "' + rel + '"', 1)
            changed = True
        elif existing is None:
            _log(notes, "fix", comp, "%s: model block added (%s)"
                 % (os.path.basename(mod_path), rel))
            if not check_only:
                block = MODEL_BLOCK % {"path": rel}
                if content.rstrip().endswith(")"):
                    content = content.rstrip()[:-1] + block
                else:
                    _log(notes, "error", comp, "%s: cannot insert model block"
                         % os.path.basename(mod_path))
                    return True
            changed = True

    if changed and not check_only:
        with open(mod_path, "w", encoding="utf-8") as fh:
            fh.write(content)
    return changed


def fix_symbol(sym_path, comp_base, footprint_name, notes, comp, check_only):
    with open(sym_path, encoding="utf-8") as fh:
        content = fh.read()
    m = re.search(r'\(property "Footprint" "([^":]+):([^"]+)"', content)
    if not m:
        _log(notes, "warning", comp, "%s: no Footprint property" % os.path.basename(sym_path))
        return False
    if m.group(1) == comp_base and m.group(2) == footprint_name:
        return False
    _log(notes, "fix", comp, "%s: Footprint property -> %s:%s"
         % (os.path.basename(sym_path), comp_base, footprint_name))
    if not check_only:
        content = content.replace(
            '(property "Footprint" "%s:%s"' % (m.group(1), m.group(2)),
            '(property "Footprint" "%s:%s"' % (comp_base, footprint_name), 1)
        with open(sym_path, "w", encoding="utf-8") as fh:
            fh.write(content)
    return True


def normalize_component(comp, check_only):
    """Return (errors, warnings, notes) for one component folder."""
    comp = os.path.abspath(comp)
    comp_base = os.path.basename(comp)
    notes = []
    errors = []
    warnings = []

    def add(severity, msg):
        _log(notes, severity, comp_base, msg)
        if severity == "error":
            errors.append((severity, comp_base, msg))
        elif severity == "warning":
            warnings.append((severity, comp_base, msg))

    if comp_base != comp_base.strip():
        add("fix", "folder name has trailing space")
        if not check_only:
            clean = os.path.join(os.path.dirname(comp), comp_base.strip())
            if os.path.exists(clean):
                add("error", "cannot rename: %s already exists" % os.path.basename(clean))
            else:
                os.rename(comp, clean)
                comp = clean
                comp_base = comp_base.strip()

    shapes_dir = find_shapes_dir(comp)
    wanted = os.path.join(comp, comp_base + ".3dshape")
    if shapes_dir and os.path.basename(shapes_dir) != os.path.basename(wanted):
        add("fix", "shapes dir %s -> %s"
            % (os.path.basename(shapes_dir), os.path.basename(wanted)))
        if not check_only:
            os.rename(shapes_dir, wanted)
            shapes_dir = wanted

    model_file = find_model_file(shapes_dir)

    # loose root .step files
    for entry in os.listdir(comp):
        full = os.path.join(comp, entry)
        if not os.path.isfile(full) or not entry.lower().endswith(".step"):
            continue
        if shapes_dir is None:
            add("fix", "moved loose %s into new %s/" % (entry, comp_base + ".3dshape"))
            if not check_only:
                os.makedirs(wanted, exist_ok=True)
                shutil.move(full, os.path.join(wanted, entry))
                shapes_dir = wanted
                model_file = find_model_file(shapes_dir)
            continue
        twin = os.path.join(shapes_dir, entry)
        if os.path.exists(twin) and open(full, "rb").read() == open(twin, "rb").read():
            add("fix", "removed duplicate root %s" % entry)
            if not check_only:
                os.remove(full)
        else:
            add("warning", "root %s has no identical twin in %s/ (left in place)"
                % (entry, os.path.basename(shapes_dir)))

    # .pretty folder
    pretty = os.path.join(comp, comp_base + ".pretty")
    for entry in os.listdir(comp):
        full = os.path.join(comp, entry)
        if os.path.isdir(full) and entry.endswith(".pretty") and full != pretty:
            if os.path.exists(pretty):
                add("error", "two .pretty folders: %s and %s" % (entry, comp_base + ".pretty"))
            else:
                add("fix", "pretty %s -> %s" % (entry, comp_base + ".pretty"))
                if not check_only:
                    os.rename(full, pretty)
    if not os.path.isdir(pretty):
        if check_only:
            add("error", "missing %s.pretty/" % comp_base)
        else:
            os.makedirs(pretty, exist_ok=True)

    # footprints
    for entry in os.listdir(comp):
        full = os.path.join(comp, entry)
        if not os.path.isfile(full) or not entry.endswith(".kicad_mod"):
            continue
        dst = os.path.join(pretty, entry)
        if os.path.exists(dst):
            if open(full, "rb").read() == open(dst, "rb").read():
                add("fix", "removed duplicate root %s" % entry)
                if not check_only:
                    os.remove(full)
            else:
                add("error", "root %s differs from %s/" % (entry, comp_base + ".pretty"))
        else:
            add("fix", "moved %s -> %s/" % (entry, comp_base + ".pretty"))
            if not check_only:
                shutil.move(full, dst)

    fp_names = []
    if os.path.isdir(pretty):
        for entry in sorted(os.listdir(pretty)):
            if entry.endswith(".kicad_mod"):
                fp_names.append(entry[: -len(".kicad_mod")])
                if not check_only:
                    fix_footprint(os.path.join(pretty, entry), shapes_dir, model_file,
                                  notes, comp_base, check_only)
        if not fp_names:
            add("error", "no footprints inside %s.pretty/" % comp_base)

    # symbols
    syms = [e for e in os.listdir(comp) if e.endswith(".kicad_sym")]
    if len(syms) != 1:
        add("error", "expected 1 .kicad_sym, found %d" % len(syms))
    elif fp_names:
        if not check_only:
            fix_symbol(os.path.join(comp, syms[0]), comp_base, fp_names[0],
                       notes, comp_base, check_only)
        else:
            content = open(os.path.join(comp, syms[0]), encoding="utf-8").read()
            m = re.search(r'\(property "Footprint" "([^":]+):([^"]+)"', content)
            if not m:
                add("error", "symbol has no Footprint property")
            else:
                if m.group(1) != comp_base:
                    add("fix", "symbol lib prefix %r -> %r" % (m.group(1), comp_base))
                if m.group(2) != fp_names[0]:
                    add("fix", "symbol footprint name %s -> %s" % (m.group(2), fp_names[0]))

    if shapes_dir is None:
        add("note", "no 3D model available (download without step, or 3D not provided)")

    fixes = [n for n in notes if n[0] == "fix"]
    notes = [n for n in notes if n[0] == "note"]
    return errors, warnings, fixes, notes


def main():
    args = [a for a in sys.argv[1:]]
    check_only = "--check" in args
    args = [a for a in args if a != "--check"]

    if args:
        comps = [os.path.abspath(a) for a in args]
    else:
        comps = sorted(os.path.join(ROOT, e) for e in os.listdir(ROOT)
                       if os.path.isdir(os.path.join(ROOT, e))
                       and not e.startswith(".")
                       and e not in SKIP_DIRS)

    total_errors = 0
    for comp in comps:
        if not os.path.isdir(comp):
            print("SKIP: not a directory: %s" % comp)
            continue
        print("== %s" % os.path.basename(comp))
        errors, warnings, fixes, notes = normalize_component(comp, check_only)
        for _, _, msg in fixes:
            print("  [FIX] %s: %s" % (os.path.basename(comp), msg))
        for _, _, msg in warnings:
            print("  [WARNING] %s: %s" % (os.path.basename(comp), msg))
        for _, _, msg in notes:
            print("  [note] %s: %s" % (os.path.basename(comp), msg))
        for _, _, msg in errors:
            print("  [ERROR] %s: %s" % (os.path.basename(comp), msg))
        total_errors += len(errors)

    mode = "CHECK" if check_only else "NORMALIZE"
    print("\n%s: %d component(s), %d error(s)" % (mode, len(comps), total_errors))
    sys.exit(1 if total_errors else 0)


if __name__ == "__main__":
    main()