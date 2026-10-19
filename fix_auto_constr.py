#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Точечные исправления последствий add_auto_categories.py.

1) hyundai: формы спецтехники (h305, экскаватор и т.п.) ошибочно попали в
   автомобильную группу хёндай — выносим их в отдельную группу;
2) doosan / vaz / uaz: добавляем русские разговорные формы в правильные
   существующие группы.
Идемпотентно.
"""
from collections import Counter

SRC = 'synonyms_clean.txt'


def normkey(s):
    return ''.join(ch for ch in s.lower() if ch.isalnum())


def load(path):
    groups = []
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line:
                groups.append([t.strip() for t in line.split(',') if t.strip()])
    return groups


def save(path, groups):
    def canon(tokens):
        seen, out = set(), []
        for t in sorted(set(tokens), key=lambda x: (normkey(x), x)):
            k = normkey(t)
            if k not in seen:
                seen.add(k)
                out.append(t)
        return out
    final = [canon(g) for g in groups if g]
    seen_keys, deduped = set(), []
    for g in final:
        k = tuple(sorted(normkey(t) for t in g))
        if k in seen_keys:
            continue
        seen_keys.add(k)
        deduped.append(g)
    deduped.sort(key=lambda g: (normkey(g[0]), g[0]))
    with open(path, 'w', encoding='utf-8') as fh:
        for g in deduped:
            fh.write(','.join(g) + '\n')
    return len(deduped)


CONSTR_HYUNDAI_KEYS = {normkey(x) for x in (
    'h305 хендай', 'хендай экскаватор', 'хюндай 300',
    'hd hyundai construction equipment', 'hyundai construction equipment',
)}

# целевые дополнения: (кандидаты имени группы, новые токены)
TARGETS = [
    (('vaz', 'lada', 'ваз', 'лада'), ['вазовский']),
    (('uaz', 'уаз'), ['уазик', 'буханка уаз']),
    (('doosan', 'доосан', 'дусан'), ['доосан', 'дусан', 'doosan dx140', 'дусан 225']),
]


def find_group(idx, names):
    """Группа, содержащая максимум кандидатов names (по normkey)."""
    cands = []
    for n in names:
        gi = idx.get(normkey(n))
        if gi is not None:
            cands.append(gi)
    cnt = Counter(cands)
    return cnt.most_common(1)[0][0] if cnt else None


def main():
    groups = load(SRC)
    idx = {}
    for gi, g in enumerate(groups):
        for t in g:
            idx.setdefault(normkey(t), gi)

    actions = []

    # 1) hyundai: вычистить спецтехнику из авто-группы
    hi = idx.get(normkey('hyundai'))
    if hi is not None:
        g = groups[hi]
        if 'construction equipment' not in ' '.join(g):
            moved = [t for t in g if normkey(t) in CONSTR_HYUNDAI_KEYS]
            if moved:
                for t in moved:
                    g.remove(t)
                newg = ['hyundai construction equipment'] + moved
                groups.append(newg)
                for t in newg:
                    idx[normkey(t)] = len(groups) - 1
                actions.append(f'hyundai: moved {moved} to separate constr group')

    # 2-3) целевые дополнения
    for names, tokens in TARGETS:
        gi = find_group(idx, names)
        if gi is None:
            actions.append(f'{names[0]}: group not found')
            continue
        g = groups[gi]
        ek = {normkey(t) for t in g}
        for t in tokens:
            k = normkey(t)
            if k in ek:
                continue
            other = idx.get(k)
            if other is not None and other != gi:
                actions.append(f'{names[0]}:{t} already in group {other}, skip')
                continue
            g.append(t)
            ek.add(k)
            idx[k] = gi
            actions.append(f'{names[0]}: added {t}')

    n = save(SRC, groups)
    print('\n'.join(actions) if actions else 'no changes')
    print(f'total_groups={n}')


if __name__ == '__main__':
    main()
