#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Курационное дополнение словаря категориями «Автомобили» и «Спецтехника».

Читает synonyms_clean.txt, дополняет существующие группы брендов
формами из auto_constr_data.py (AUTO_BRANDS / CONSTR_BRANDS) и создаёт
новые группы для отсутствующих брендов. Идемпотентен: повторный запуск
ничего не меняет.

Правила безопасности:
  * если токены бренда пересекаются более чем с одной существующей
    группой — это конфликт, группа не трогается (фиксируется в отчёте);
  * дубликаты по normkey внутри группы удаляются;
  * идентичные группы склеиваются;
  * итоговый файл сортируется и канонизируется так же, как в основном
    конвейере build_clean_dictionary.py.
"""
from auto_constr_data import AUTO_BRANDS, CONSTR_BRANDS

SRC = 'synonyms_clean.txt'
REPORT = 'auto_constr_report.log'


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


def canon_group(tokens):
    seen, out = set(), []
    for t in sorted(set(tokens), key=lambda x: (normkey(x), x)):
        k = normkey(t)
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def main():
    groups = load(SRC)
    key_index = {}
    for gi, g in enumerate(groups):
        for t in g:
            key_index.setdefault(normkey(t), set()).add(gi)

    added_groups = appended_tokens = skipped_conflict = 0
    report = []
    for section, table in (('AUTO', AUTO_BRANDS), ('CONSTR', CONSTR_BRANDS)):
        for canon, variants in table.items():
            toks = [canon] + [v.strip() for v in variants if v.strip()]
            keys = [normkey(t) for t in toks]
            hits = set()
            for k in keys:
                hits |= key_index.get(k, set())
            if len(hits) == 1:
                gi = next(iter(hits))
                existing_keys = {normkey(t) for t in groups[gi]}
                for t, k in zip(toks, keys):
                    if k not in existing_keys:
                        groups[gi].append(t)
                        existing_keys.add(k)
                        key_index.setdefault(k, set()).add(gi)
                        appended_tokens += 1
            elif len(hits) > 1:
                skipped_conflict += 1
                report.append(f'SKIP conflict {section}:{canon} -> groups {sorted(hits)}')
            else:
                gi = len(groups)
                groups.append(toks)
                for t, k in zip(toks, keys):
                    key_index.setdefault(k, set()).add(gi)
                added_groups += 1
                appended_tokens += len(toks)
                report.append(f'NEW group {section}:{canon}')

    final = [canon_group(g) for g in groups if g]
    seen_keys, deduped = set(), []
    for g in final:
        k = tuple(sorted(normkey(t) for t in g))
        if k in seen_keys:
            continue
        seen_keys.add(k)
        deduped.append(g)
    deduped.sort(key=lambda g: (normkey(g[0]), g[0]))

    with open(SRC, 'w', encoding='utf-8') as fh:
        for g in deduped:
            fh.write(','.join(g) + '\n')

    with open(REPORT, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')

    print(f'added_groups={added_groups} appended_tokens={appended_tokens} '
          f'conflicts_skipped={skipped_conflict} total_groups={len(deduped)}')


if __name__ == '__main__':
    main()
