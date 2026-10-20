#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Smoke-тесты финального словаря synonyms_clean.txt (продакшен-валидация)."""
import sys, re

PATH = "synonyms_clean.txt"
rows = []
with open(PATH, encoding="utf-8") as f:
    for ln in f:
        ln = ln.strip()
        if ln: rows.append(ln.split(","))

fails = []
def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL"), name, detail)
    if not ok: fails.append(name)

# 1. обязательные бренды присутствуют
must = {"makita": ["макита"], "milwaukee": ["милуоки"], "bosch": ["бош"],
        "dewalt": [], "hilti": ["хилти"], "metabo": ["метабо"]}
idx = {}
for r in rows:
    for w in r: idx.setdefault(w, []).append(r)
for brand, cyr in must.items():
    found = [r for r in rows if r[0] == brand or brand in r]
    ok = bool(found) and any(r[0] == brand for r in found)
    check(f"brand {brand} present & canonical-first", ok,
          ",".join(found[0])[:120] if found else "")
    if cyr and found:
        best = max(found, key=len)
        check(f"{brand} has cyrillic form {cyr}", any(c in best for c in cyr), ",".join(best)[:120])

# 2. makita: модельные хвосты схлопнуты, опечатки сохранены
mk = next((r for r in rows if "makita" in r), [])
check("makita no model tails", not any(re.search(r"(xgt|lxt|40v|starlock|bl)\b", x) for x in mk), ",".join(mk))
check("makita keeps typos", any(x in mk for x in ("макетра","махита","микита","макета")), ",".join(mk))

# 3. milwaukee one-key схлопнута в milwaukee
mw = [r for r in rows if any("milwaukee" in x or "милуоки" in x for x in r)]
check("one key collapsed", not any("one key" in x or "onekey" in x for r in mw for x in r),
      "; ".join(",".join(r)[:80] for r in mw[:3]))

# 4. нет скобок/юрлиц/доменов в выводе
bad = [r for r in rows if any("(" in x or ")" in x or re.search(r"\.(com|ru)$|\bgmbh\b|\bооо\b", x) for x in r)]
check("no brackets/legal/domains", len(bad) == 0, str(bad[:3]))

# 5. формат ES: без подстрок внутри строки, <= MAX_SYNS
sub = [r for r in rows if any(a != b and a in b for a in r for b in r)]
check("no substring pairs", len(sub) == 0, str(sub[:3]))
check("row length <= 12", all(len(r) <= 12 for r in rows))

# 6. первая форма — самая каноничная: латиница раньше транслит-опечатки? эвристика:
#    если в строке есть точное якорное имя, оно должно быть первым
ANCH = {"makita","milwaukee","bosch","dewalt","hilti","metabo","karcher","ryobi"}
viol = [r for r in rows if (set(r) & ANCH) and r[0] not in ANCH]
check("anchor first when present", len(viol) == 0, str(viol[:3]))

print(f"\n{'ALL PASS' if not fails else 'FAILED: ' + ', '.join(fails)} ({len(rows)} rows)")
sys.exit(1 if fails else 0)
