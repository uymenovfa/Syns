#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Чистка словаря синонимов брендов/производителей для Elasticsearch (synonyms.txt, формат CSV).

Что делает (см. README_CLEAN.md):
 1) нормализация (Ё->е, lower, пробелы);
 2) union-find склейка пересекающихся строк + дедуп;
 3) вырезание юрлицо-хвостов (ООО/PAO/GMBH...), доменов (.com/.ru), гео-уточнений
    (германия/россия), шумовых слов (профессионал/промышленный/инструмент...),
    модельных хвостов и товарных линеек (bosch 18v li / makita lxt / hilti nuron / zubr xr);
 4) слияние вариантов, НЕ меняющих первоначальное слово: ё-двойники, опечатки
   (ботш/boosh), транслит (zavod imeni likhacheva == завод имени лихачева),
   приставочные формы (деволт промышленный -> деволт);
 5) отсечение нерелевантного сегменту B2B маркетплейса мусора (FM-радио, песни,
    рестораны, книжные издательства, «завод имени N-го», одиночные коды/цифры...)
    через стоп-слова и фильтр «популярность»: бренд остаётся, только если он
    встречается в файле достаточно часто (несколько вариантов написания — как раз
    признак популярного бренда наподобие статистики маркетплейсов).
 6) валидация формата ES synonyms file.

Выход: synonyms_clean.txt, clean_report.txt
"""

import re
import sys
import unicodedata
from collections import Counter, defaultdict

SRC = "synonyms.txt"
DST = "synonyms_clean.txt"
RPT = "clean_report.txt"

# ---------------------------------------------------------------- нормализация

def norm(s: str) -> str:
    s = s.replace("\ufeff", "")
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("Ё", "е").replace("ё", "е")
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s.strip(" .,;:")

def has_cyr(s): return bool(re.search(r"[а-я]", s))
def has_lat(s): return bool(re.search(r"[a-z]", s))
def tokens(s): return [t for t in re.split(r"[ \-/–—]+", s) if t]

# ------------------------------------------------------------------ стоп-списки

LEGAL = set("""
inc llc ltd limited plc ag co corp corporation company companies gmbh mbh kg ohg se sa sas
srl sl spa nv bv ab as oy pte pvt ltda bhd sdn tbsk jsc pjsc ojsc oao zao dao pa pc group
holdings holding international global worldwide technologies technology systems system
services service solutions enterprises enterprise industries industry trading trade
distribution sales marketing development investments capital partners
ооо пао ао зао оао ип нко гуп муп чуп уп
предприятие предприятие объединение компания корпорация акционерное общество товарищество
производственное государственное унитарное коммерческое холдинг группа
""".split())

# слова, НЕ меняющие сам бренд (приставки/уточнения) -> вырезаются из имени.
# Только точные совпадения, чтобы не съесть часть названия (промкомпрессор != промышленный).
NOISE_RE = re.compile(
    r"^(?:"
    r"профессионал|профессиональная|профессиональные|промышленный|промышленная|промышленное|"
    r"промышленные|бытовой|бытовая|любительский|товарный|фирменный|родной|заводской|оригинал|"
    r"original|genuine|official|brand|марка|бренд|знак|название|professional|"
    r"германия|германии|germany|deutschland|немецкая|швеция|швеции|Sweden|шведская|"
    r"италия|италии|italy|испания|испании|spain|турция|turkey|китай|china|корейская|korea|"
    r"япония|japan|france|французская|англии|britain|британская|великобритания|"
    r"россия|russia|российская|украина|ukraine|белоруссия|belarus|грузия|georgia|"
    r"польша|poland|чехия|czech|нидерланды|netherlands|"
    r"инструмент|электроинструмент|крепеж|запчасти|аксессуары|"
    r"shop|store|online|marketplace|центр|магазин|"
    r"имени|им\.|им|imeni"
    r")$", re.UNICODE)

DOMAIN_RE = re.compile(r"\.(com|net|org|ru|su|rf|de|uk|fr|it|es|pl|cz|jp|cn|in|info|biz|shop|store|io|ai|tech|online|site|pro|eu|us|ca|au|ch|at|be|nl|dk|se|no|fi|by|kz|ua|lv|lt|ee)(\.[a-z]{2})?$")

# «имена собственные не-бренды» — явные стоп-паттерны целых строк
BAD_LINE_RE = re.compile(
    r"(?:"
    r"^\d{4,}\b"                                      # 118360, 00159586128...
    r"|\b\d{2,3}(?:[.,]\d)?\s*(?:fm|ам)\b"            # 103.7 fm / 104.5fm радио
    r"|(?:^|, *)(?:радио|телеканал|кинотеатр|театр|библиотека|музей|церковь|храм|мечеть|"
    r"школа|гимназия|лицей|университет|институт|академия|колледж|детсад|"
    r"ресторан|кафе|бар|столовая|гостиница|отель|санаторий|лагерь)"
    r"|футбольн|хоккейн|баскетбол|спортивн|фитнес|радиостанц"
    r"|издательств|книжный|газета|журнал|редакция|киностудия"
    r"|благотворительн|партия|община|приход|епархи"
    r"|министерств|ведомств|администрац|сельсовет|муниципал"
    r")", re.I)

# токен-«не бренд»: модели/линейки/юрлицо-суффиксы с точками/гео-уточнения в скобках.
MODEL_TOKEN_RE = re.compile(
    r"^(?:"
    r"\d{1,3}[vvlw](?:max|li[-]?ion|ion|x|ac|dc|ch|ah)?"              # 18v 40v max
    r"|lxt|xgt|cas|neuron|nuron|flexvolt|flex[-]?volt|xr|xtr|coolpack|procore|atomic|"
    r"brushless|starlock|li[-]?ion|easycut|aerotwin|flat[-]?wiper|firestop|kollex|tonson|nmop|"
    r"b\d{2}|te[-]?\d{3,4}|dw[-]?\d{3,4}|dcd[-]?\d{3,4}|dcf[-]?\d{3,4}|ga[-]?\d{3,4}|"
    r"sb[-]?\d{3,4}|bs[-]?\d{3,4}|hr\d{2,4}|ht\d{2,4}|gf\d{2,4}|gmf\d{2,4}|kt\d{2,4}|"
    r"sha\d{3,4}|dia[-]?\d{3,4}|en[-]?\d{3,4}|p\d{2,4}[a-z]{0,3}|"
    r"[a-z]{1,3}\d{3,5}[a-z]{0,3}|\d{3,5}[a-z]{1,3}"                 # ct18 dw4210 1069bdc
    r"|\((?:germany|deutschland|france|italia|italy|spain|portugal|japan|korea|china|"
    r"india|brazil|mexico|canada|usa|u\.?s\.?a?\.?|united states|united kingdom|uk|"
    r"england|switzerland|sweden|norway|denmark|finland|poland|czech|czech republic|"
    r"slovakia|hungary|romania|bulgaria|serbia|croatia|slovenia|austria|belgium|"
    r"netherlands|holland|turkey|israel|iran|egypt|south africa|australia|new zealand|"
    r"argentina|colombia|chile|peru|venezuela|thailand|vietnam|malaysia|singapore|"
    r"indonesia|philippines|pakistan|bangladesh|uae|qatar|saudi arabia|kuwait|bahrain|"
    r"oman|yemen|jordan|lebanon|morocco|algeria|tunisia|libya|nigeria|kenya|ghana|"
    r"ethiopia|ukraine|belarus|kazakhstan|uzbekistan|georgia|armenia|azerbaijan|"
    r"moldova|estonia|latvia|lithuania|rossiya|russia|россия| украина| германия|"
    r"company|corporation|inc|ltd|limited|gmbh|ag|sa|s\.?a\.?|srl|spa|nv|bv|ab|as|oy|"
    r"pte|jsc|pjsc|ojsc|oao|zao|dao|llc|plc|kg|ohg|se)"               # (germany), (company)...
    r"|s\.?a\.?(?: de c\.?v\.?)?|s\.?r\.?o\.?(?:\.?s\.?a\.?)?|s\.?a\.?s?\.?|"
    r"l\.?t\.?d\.?a\.?|a\.?g\.?(?:& ?co\.? ?kg)?|d\.?o\.?o\.?|b\.?v\.?|n\.?v\.?|"
    r"p\.?l\.?c\.?|c\.?v\.?a\.?|s\.?p\.?a\.?$"                  # юр. суффиксы с точками
    r"|(?:and|&) ?co\.?|co\.?kg|company limited|limited liability"
    r")$", re.I)

RU_ADJ_SUFFIXES = ("ский","ская","ское","ские","ого","овый","евый","ийский","инский")

def is_legal_tok(t):
    if t in LEGAL or DOMAIN_RE.search(t): return True
    if NOISE_RE.match(t):
        # одиночное кириллическое слово — часть названия, не шум
        # (шум режется только в многословных именах: "bosch профессионал", "kerher германия")
        return False
    return False

def strip_noise(name):
    toks = tokens(name)
    def drop(t):
        if t in LEGAL or DOMAIN_RE.search(t): return True
        if len(toks) > 1 and NOISE_RE.match(t): return True   # шум только во многословных
        return False
    kept = [t for t in toks if not drop(t)]
    return " ".join(kept) if kept else ""

def core_tokens(name):
    """токены 'ядра' бренда (без шума/юрлица/домена/моделей)"""
    out = []
    for t in tokens(name):
        if is_legal_tok(t) or MODEL_TOKEN_RE.match(t): continue
        out.append(t)
    return out

def is_bad_line(name):
    return bool(BAD_LINE_RE.search(name))

def looks_like_garbage(name):
    toks = tokens(name)
    if not toks: return True
    core = core_tokens(name)
    if not core: return True                       # весь набор — шум/юрлицо
    # домен целиком (1000flies.com) без ядра
    if all(DOMAIN_RE.search(t) for t in toks): return True
    # только цифры/коды
    if re.fullmatch(r"[\d\s./\-xX]+", name): return True
    if len(core) and re.fullmatch(r"\d{4,}", core[0]) and len(core) <= 2: return True
    # «1/2 m», «(1873) 1894 - 1934» и т.п.
    if re.match(r"^[^a-zа-я]+$", name): return True
    return False

# ------------------------------------------------------------- похожесть/транслит

try:
    from rapidfuzz.distance import Levenshtein
    def lev(a, b): return Levenshtein.distance(a, b)
except Exception:
    def lev(a, b):
        la, lb = len(a), len(b)
        if abs(la - lb) > 4: return 99
        prev = list(range(lb + 1))
        for i, ca in enumerate(a, 1):
            cur = [i] + [0] * lb
            for j, cb in enumerate(b, 1):
                cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            prev = cur
        return prev[lb]

TR = {"а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ж":"zh","з":"z","и":"i","й":"y",
"к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f",
"х":"kh","ц":"ts","ч":"ch","ш":"sh","щ":"sch","ы":"y","э":"e","ю":"yu","я":"ya","ъ":"","ь":""}

def translit(s):
    return "".join(TR.get(c, c) for c in s)

def same_translit(a, b):
    """латинский вариант == транслит кириллического (zavod imeni likhacheva / завод им. лихачева)"""
    if (has_cyr(a) and has_lat(b) and not has_cyr(b)) or (has_cyr(b) and has_lat(a) and not has_cyr(a)):
        lat, cyr = (b, a) if has_cyr(a) else (a, b)
        tr = re.sub(r"[^a-z0-9 ]", "", translit(" ".join(tokens(cyr))).replace("'", ""))
        latn = re.sub(r"[^a-z0-9 ]", "", lat)
        tr = re.sub(r"\b(im|imeni)\b", " ", tr)
        tr = re.sub(r"\s+", " ", tr).strip()
        if tr == latn: return True
        if len(tr) >= 5 and lev(tr, latn) <= max(1, len(latn) // 6): return True
    return False

def typo_match(a, b):
    """варианты одного слова с опечаткой/суффиксом: бош/ботш, макита/макитка, самсунг/самсунговский"""
    d = lev(a, b)
    L = max(len(a), len(b))
    if L < 3 or d == 0: return False
    if d == 1: return True
    if d <= 2 and L >= 6 and d / L <= 0.3: return True
    if d <= 2 and L >= 4 and d / L <= 0.4 and (has_cyr(a) == has_cyr(b)): return True
    # русская суффиксная форма прилагательного от того же корня
    if has_cyr(a) and has_cyr(b):
        long_s, short = (a, b) if len(a) >= len(b) else (b, a)
        if len(short) >= 4 and long_s.startswith(short[:max(4, len(short)-2)]) and \
           any(long_s.endswith(sfx) for sfx in RU_ADJ_SUFFIXES) and len(long_s) - len(short) <= 7:
            return True
    return False

def prefix_variant(big, small):
    """small — 'ядро' big, лишние токены big — только шум/гео/юрлицо/модель.
       devolt промышленный -> devolt ; bosch auto parts -> bosch"""
    cb, cs = core_tokens(big), core_tokens(small)
    if not cs or len(cs) >= len(cb): return False
    if cb[0] != cs[0] and not typo_match(cb[0], cs[0]): return False
    st = set(tokens(small))
    rest = [t for t in tokens(big) if t not in st]
    return bool(rest) and all(NOISE_RE.match(t) or t in LEGAL or DOMAIN_RE.search(t)
                              or MODEL_TOKEN_RE.match(t) for t in rest)

# ------------------------------------------------------------ популярность бренда
#
# Файл собран так, что у РЕАЛЬНО популярных брендов много вариантов написания
# (латиница + кириллица + опечатки + ё-формы) — это прокси метрики popularity
# с маркетплейсов. У мусорных строк обычно 1-2 варианта.
# Дополнительно: якорный список гарантированно известных B2B/потребительских брендов.

ANCHORS = set(map(norm, """
bosch бош siemens сименс hilti хилти makita макита dewalt делтал деволт metabo метабо
stihl штиль стигль husqvarna хускварна karcher керхер кёрхер hitachi хитати panasonic панасоник
milwaukee милуоки ryobi риоби ken интерскол enkor энкор zubr зубр welba viking викинг
knauf кнауф saint-gobain сен-гобен gyproc danfoss данфосс grundfos грундфос wilo вило lowara
pedrollo ridgid риджид uponor apaponor valtec far rubineti camozzi smc festo parker rexroth
schneider legrand abb iek ekf tdm кэаз овен technoNICOL технониколь izover promrukav sevкабель
uzxm uzox metallprofil profiner ariston baxi vaillant viessmann protherm buderus bosch thermotechnik
hyundai хюндай kia киа toyota тойота nissan ниссан honda хонда ford форд volkswagen фольксваген
bmw mercedes мерседес renault рено chevrolet geely chery changan haval exeed kamaz камаз
gaz газ zil зиЛ лихачев uaz уаз moskvich москвич lada лада ваз umpo Sollers
caterpillar komatsu liebherr jcb terex bobcat scania man iveco daf
samsung самсунг lg apple huawei xiaomi mi philips sony toshiba canon epson dyson
dewalt stadil стадил resanta ресанта fiolent denzel kitfort greenworks energizer duracell varta
robitron elara deko gauss osram ledvance navigator волга электросвет кометос феникс легранд
aBB swe-spb ssw элтехпроект
""".split()))

STOP_FIRST_TOKENS = set(map(norm, """
radioRecords music song songs clip band album label cafe coffee restaurant pizza food lokum
book books library magazine newspaper journal news press tv film cinema movie studio studios
records theater theatre museum church temple mosque university institute academy college school
bank finance insurance fund foundation party movement team fc fk games casino lottery poker bet
ministry administration government army military hospital clinic pharmacy medical dental
""".split()))

def popularity_score(names):
    """число уникальных ядерных форм + бонус за кириллицу+латиницу одновременно"""
    cores = set()
    for n in names:
        c = " ".join(core_tokens(n))
        if c: cores.add(c)
    scr = sum(len(tokens(c)) for c in cores)
    return len(cores), scr

# -------------------------------------------------------------------------------

def load_lines(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.rstrip("\n")
            if not ln.strip(): continue
            vs = [norm(v) for v in ln.split(",")]
            vs = [v for v in vs if v]
            if vs: rows.append(vs)
    return rows

class DSU:
    def __init__(self): self.p = {}
    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]; x = self.p[x]
        return x
    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb: self.p[rb] = ra

def main():
    rows = load_lines(SRC)
    dsu = DSU()
    for vs in rows:
        for v in vs[1:]:
            dsu.union(vs[0], v)

    # вторая волна: склейка строк с общим «ядром» (без юрлиц/шума),
    # чтобы "bosch" + "bosch профессионал" + "бош" оказались в одной группе
    key_owner = {}
    def keys_of(v):
        out = []
        c = strip_noise(v)
        tk = core_tokens(c) if c else []
        if len(tk) >= 2:
            out.append(" ".join(tk[:2]))
        elif len(tk) == 1 and len(tk[0]) >= 5 and has_lat(tk[0]):
            out.append(tk[0])
        return out
    for vs in rows:
        for v in vs:
            for k in keys_of(v):
                if k in key_owner:
                    dsu.union(key_owner[k], v)
                else:
                    key_owner[k] = v

    groups = defaultdict(set)
    for vs in rows:
        for v in vs:
            groups[dsu.find(v)].add(v)
    print(f"[i] исходных строк: {len(rows)}, групп после union-find: {len(groups)}", file=sys.stderr)

    removed = []          # (reason, item)
    final_rows = []

    for g in groups.values():
        orig_first = sorted(g)[0]
        # 1) строки-«не бренд» целиком
        joined = ",".join(sorted(g))
        if is_bad_line(joined) or any(is_bad_line(v) for v in g):
            bad = [v for v in g if is_bad_line("," + v)]
            if len(bad) >= max(1, len(g)//2):
                removed.append(("not_a_brand", orig_first)); continue

        # 2) чистим варианты: вырезаем шум/юрлица/домены, отбрасываем мусор
        cleaned_names = []
        for v in g:
            c = strip_noise(v)
            if not c or looks_like_garbage(c): continue
            cleaned_names.append(c)
        if not cleaned_names:
            removed.append(("garbage_after_clean", orig_first)); continue

        # 3) слияние внутри группы: приставочные/опечаточные/транслит-варианты
        cleaned_names.sort(key=lambda s: (-len(core_tokens(s)), len(s)))
        merged = []            # списки токенов канонических имён
        alias_of = defaultdict(list)
        for n in cleaned_names:
            placed = False
            for i, m in enumerate(merged):
                if prefix_variant(m, n) or prefix_variant(n, m) or same_translit(m, n) or \
                   (len(tokens(m)) == len(tokens(n)) and typo_match(m, n)) or \
                   (tokens(m)[0] == tokens(n)[0] and len(tokens(n)) == 1 and len(tokens(m)) > 1):
                    # выбрать более короткое/каноничное как голову
                    if len(core_tokens(n)) < len(core_tokens(m)) or \
                       (len(core_tokens(n)) == len(core_tokens(m)) and len(n) < len(m)):
                        merged[i] = n
                    alias_of[i].append(n)
                    placed = True
                    break
            if not placed:
                merged.append(n)

        merged = list(dict.fromkeys(merged))

        # 4) релевантность/популярность
        n_cores, _ = popularity_score(merged)
        anchor = any(any(w in ANCHORS for w in tokens(m)) or m in ANCHORS for m in merged)
        first_tok = tokens(merged[0])[0] if merged else ""
        stop = first_tok in STOP_FIRST_TOKENS
        keep = anchor or (not stop and n_cores >= 3)
        if not keep:
            reason = "stopped_topic" if stop else "low_popularity"
            removed.append((reason, " | ".join(merged))); continue

        # 5) финальная форма каждой строки: канон + частотные опечатки оставляем только
        #    если они реально отличаются буквой е/ё или это известные варианты.
        row = sorted(set(merged))
        # ES-валидность: без «>», без пустых, без повторов
        row = [r for r in row if r and ">" not in r]
        if len(row) < 2:
            removed.append(("single_form_left", row[0] if row else orig_first)); continue
        final_rows.append(row)

    # дедуп identical rows
    seen = set(); out = []
    for r in sorted(final_rows, key=lambda x: x[0]):
        k = tuple(r)
        if k in seen: continue
        seen.add(k); out.append(r)

    with open(DST, "w", encoding="utf-8") as f:
        for r in out:
            f.write(",".join(r) + "\n")

    cnt = Counter(reason for reason, _ in removed)
    with open(RPT, "w", encoding="utf-8") as f:
        f.write(f"всего групп: {len(groups)}\nоставлено строк: {len(out)}\nудалено групп: {len(removed)}\n\n")
        f.write("причины удаления:\n")
        for k, v in cnt.most_common(): f.write(f"  {k}: {v}\n")
        f.write("\n== примеры удалённого ==\n")
        shown = defaultdict(int)
        for reason, item in removed:
            if shown[reason] < 60:
                f.write(f"[{reason}] {item[:220]}\n"); shown[reason] += 1
        f.write("\n== примеры изменённых известных брендов ==\n")
        for probe in ["bosch","dewalt","makita","hilti","karcher","zubr","enkor","interskol","samsung"]:
            for r in out:
                if any(p in r[0] for p in [probe]) or any(probe in x for x in r):
                    f.write("  " + ",".join(r)[:220] + "\n")
    print(f"[i] оставлено={len(out)} удалено={len(removed)} ({dict(cnt)})", file=sys.stderr)

if __name__ == "__main__":
    main()
