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

# параметры качества/безопасности склейки
MIN_KEY_LEN = 5     # минимальная длина «ключа бренда» для union по ключу
MAX_LINKS = 25      # максимум склеек через один ключ (защита от омонимов-конденсаторов)
POP_MIN = 3           # минимальная частота варианта/ключа для участия в склейке и для retention
MAX_GROUP = 20      # группы больше — артефакты цепочечной склейки, разбиваются
MAX_SYNS = 16       # максимум синонимов в одной ES-строке
MAX_FORMS_PER_KEY = 8   # максимум форм одного ядра бренда (канон лат+кир + частотные опечатки)

# Якорные бренды никогда не удаляются фильтром off_topic/low_popularity —
# защита от регрессии «makita/milwaukee исчезли из словаря».
ANCHOR_PROTECT = {"bosch", "dewalt", "makita", "hilti", "milwaukee", "karcher",
    "ryobi", "metabo", "hitachi", "panasonic", "celma", "энкор", "интерскол",
    "zubr", "зубр", "стапель", "стахель", "resant", "калибр", "ватрушка",
    "патон", "солярка", "спец", "гриф", "кратон", "fit", "гамбит", "барс",
    "диолд", "skil", "aidex", "aeg", "gmc", "gt", "greenworks", "daewoo",
    "champion", "husqvarna", "stihl", "mtd", "efco", "oleo", "echo",
    "torx", "denzel", "wigam", "wilmax", "sve", "sadko", "shtenly",
    "intertool", "korfo", "konner", "sber", "prof", "prorab", "starter",
    "topex", "triomax", "yato", "wortex", "абразив", "алко", "асола",
    "бош", "делтал", "милуоки", "макита", "хилти", "кархер", "метобо",
    "риоби", "хитачи", "панасоник", "целма", "стаиглер", "дельт",
    "greenwork", "чемпион", "хускварна", "штиль", "елеко", "дензель",
    "вигам", "садо", "интерскол", "корфо", "коннер", "профи", "проораб",
    "топэкс", "триомэкс", "ято", "вортекс"}

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

# корпоративные/описательные слова, которые сами по себе не делают название
# другим брендом: makita werkzeug -> makita, bosch power tools -> bosch.
CORP_WORDS = set("""
werkzeug werkzeuge gerate equipment tools tool power hand electric electrical
engineering engineering srl srl spolka spolka s r o machinery machine machines
hardware supplies supply industrial professional expert experts division
electro electronics electronic
""".split())

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

# Тематические слова вне сегмента B2B-инструментов/оборудования.
# Если в группе ≥2 разных таких слов (или ≥1 при малой группе) — это кафе/киностудия/лейбл,
# а не производитель. Слова вроде "power tools engineering" сюда НЕ входят.
TOPIC_WORDS = set("""
coffee cafe cafeteria bakery pastry pancake donut dessert icecream ice cream yogurt milk cheese meat
pizzeria pizza restaurant diner tavern steakhouse bistro pub bar brew brewery beer wine vodka spirits
roasters roaster espresso latte cappuccino food foods snacks catering deli grocery market markets
film films movie movies cinema tv television video videos music musical song songs band album albums
records label studio studios media multimedia production productions entertainment games gaming casino
lottery betting bet poker news press publishing publisher book books library magazine journals podcast
radio television channel channels app apps software cloud hosting digital agency advertising marketing
bank banking finance fintech insurance fund funds capital investments investment loans credit mortgage
broker brokers asset assets holdings trust consultancy consulting law legal attorney attorneys
pharma pharmaceutical pharmaceuticals pharma biologics medical healthcare clinic clinics hospital
hospitals dental care wellness spa beauty cosmetics makeup perfume fragrance fashion clothing apparel
shoes jewelry hotel hotels resort travel tours airline airlines airport taxi transport logistics courier
school schools academy academies college university university education learning course courses
church temple mosque parish diocese monastery sect movement party union foundation charity nonprofit
ngo volunteer team teams fc fk hockey basketball football soccer club clubs league
""".split())

# токен-«не бренд»: модели/линейки/юрлицо-суффиксы с точками/гео-уточнения в скобках.
MODEL_TOKEN_RE = re.compile(
    r"^(?:"
    r"\d{1,3}[vvlw](?:max|li[-]?ion|ion|x|ac|dc|ch|ah)?"              # 18v 40v max
    r"|max|min|bl|nl|sds|lxt|xgt|cas|neuron|nuron|flexvolt|flex[-]?volt|xr|xtr|coolpack|procore|atomic|"
    r"brushless|starlock|li[-]?ion|easycut|aerotwin|flat[-]?wiper|firestop|kollex|tonson|nmop|"
    r"b\d{2}|te[-]?\d{3,4}|dw[-]?\d{3,4}|dcd[-]?\d{3,4}|dcf[-]?\d{3,4}|ga[-]?\d{3,4}|"
    r"sb[-]?\d{3,4}|bs[-]?\d{3,4}|hr\d{2,4}|ht\d{2,4}|gf\d{2,4}|gmf\d{2,4}|kt\d{2,4}|"
    r"sha\d{3,4}|dia[-]?\d{3,4}|en[-]?\d{3,4}|p\d{2,4}[a-z]{0,3}|"
    r"|\d{2,5}[a-z]{1,3}"                                 # ct18 dw4210 1069bdc
    r"|\d{2,5}"                                            # makita 40, hilti te 3000
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
    # одиночные модельные/линеечные префиксы и обрывки (hilti b6, te/hilti)
    r"|te|tf|hr|ht|dhr|dth|hc|sh|sw|ga|gh|gp|gsa|ha|hs|hw|up|um|us|b\d+"
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
    name = strip_brackets(name)          # скобочные уточнения вырезаем целиком
    toks = tokens(name)
    def drop(t):
        if t in LEGAL or DOMAIN_RE.search(t): return True
        if len(toks) > 1 and NOISE_RE.match(t): return True   # шум только во многословных
        return False
    kept = [t for t in toks if not drop(t)]
    return " ".join(kept) if kept else ""

def strip_brackets(s):
    """вырезаем скобочные уточнения целиком: 'makita (germany)' -> 'makita',
       'opal labs inc. (portland, or)' -> 'opal labs inc.'"""
    s = re.sub(r"\((?:[^()]*)\)", " ", s)
    return re.sub(r"\s+", " ", s).strip(" .,")

def core_tokens(name):
    """токены 'ядра' бренда (без шума/юрлица/домена/моделей/скобочных уточнений)"""
    out = []
    for t in tokens(strip_brackets(name)):
        if is_legal_tok(t) or MODEL_TOKEN_RE.match(t): continue
        if t in CORP_WORDS: continue
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
    # смешанная письменность (maketra/макета) — это НЕ одно и то же слово;
    # иначе кириллические канон-формы теряются при схлопывании
    if has_cyr(a) != has_cyr(b): return False
    d = lev(a, b)
    L = max(len(a), len(b))
    if L < 3 or d == 0: return False
    if d == 1 and abs(len(a) - len(b)) <= 2: return True
    if d <= 2 and L >= 6 and d / L <= 0.3: return True
    if d <= 2 and L >= 4 and d / L <= 0.4 and (has_cyr(a) == has_cyr(b)): return True
    # русская суффиксная форма прилагательного от того же корня
    if has_cyr(a) and has_cyr(b):
        long_s, short = (a, b) if len(a) >= len(b) else (b, a)
        if len(short) >= 4 and long_s.startswith(short[:max(4, len(short)-2)]) and \
           any(long_s.endswith(sfx) for sfx in RU_ADJ_SUFFIXES) and len(long_s) - len(short) <= 7:
            return True
    return False

# Словарь известных латинских написаний для корректной канонизации смешанных форм:
# 'dolmar долмар' -> 'dolmar', а не наоборот. Заполняется после определения ANCHORS_RAW.
LAT_KNOWN = set()

# линейки/модельные слова (в т.ч. транслит кириллицы): aerotwin / аэротвин, procore / прокор
CYR2LAT = str.maketrans("абвгдежзийклмнопрстуфхцчшщъыьэюя",
                        "abvgdezzijklmnoprstufhccshhyeeyu")  # х->x, щ->sh, ж/з->z: лхт->lxt, хгт->xgt

def _line_variants(w):
    out = {w}
    v = w.translate(CYR2LAT)
    if v != w: out.add(v)
    return out

LINE_WORDS = set()
for _w in """
lxt xgt cas neuron nuron flexvolt coolpack procore atomic easycut aerotwin firestop
kollex tonson nmop starlock brushless xtr bl sds max min li flatwiper onekey redlithium
redlithiumhd highoutput hub
лхт хгт кейс нейрон нурон флексвольт кулпак прокор атомик изикат аэротвин фаерстоп
коллекс тонсон нмоп старлок брашлесс икстр редлитиум хаб
""".split():
    LINE_WORDS |= _line_variants(_w)
# однобуквенные токены ('м18'->'m18') не добавляем в regex-Alternation — иначе
# одиночная 'm' матчит любое имя 'm...' и режет канонические формы
LINE_WORDS_RE = re.compile(
    r"^(?:" + "|".join(sorted(map(re.escape, {w for w in LINE_WORDS if len(w) > 1}))) +
    r"|[а-я]?[a-z]?\d{1,3}[vvlwh](?:max|li[-]?ion|ion|x|ac|dc|ch|ah|bdl)?" +
    r"|m\d{2}|one[- ]?key|flat[-]?wiper)$")
# хвостовые описательные слова: bosch auto parts -> bosch; makita japanese -> makita
TAIL_STOP = set("""
auto automotive parts part accessories accessory japan japanese germany german
tools tool power professional pro industrial lithium ion battery batteries cordless
electric electrical werkzeug engineering corp corporation company
te tf hr ht hm dhr dth hc dh sh sw ga gh gp gm gsa gs ha hs hw up um us
one hand onehand corded pneumatic wood metal concrete stone masonry garden outdoor
garden power tools accessories spare parts replacement original genuine compatible
twelve eighteen fuel redlithium lithium hd highoutput onekey key hub bernard flexfit flex
""".split())

# линейка/под-бренд = [модельный токен ...] + одно слово ('one key', 'redlithium',
# 'flex fit'); вырезается ЦЕЛИКОМ, иначе 'one'/'key' по отдельности матчат
# обычные названия ('one world', 'key tech') и режут канонические формы.
LINE_PHRASE_RE = re.compile(
    r"^(?:(?:[а-я]?[a-z]?\d{1,3}[vvlwh](?:max|li[-]?ion|ion|x|ac|dc|ch|ah|bdl)?|m\d{2}"
    r"|fuel|redlithium|hd)\s+)*"
    r"(?:one[\s-]?key|key|redlithium(?:hd)?|onekey|highoutput|flexfit|bernard|twelve|eighteen)$")

def strip_line_tail(name):
    """срезать модельные/линеечные/описательные хвосты: 'makita 40v max'->'makita',
       'bosch flat wiper'->'bosch', 'макита хгт'->'макита', 'milwaukee one key'->'milwaukee'.
       Хвост режется ПОСЛЕДОВАТЕЛЬНО (только совпадающие подряд), иначе
       'hilti te 3000' превратилось бы в 'hilti te' — потерянный мусор."""
    toks = tokens(strip_brackets(name))
    while len(toks) > 1 and (LINE_WORDS_RE.match(toks[-1]) or toks[-1] in TAIL_STOP):
        toks.pop()
    # фразовая линейка целиком: 'milwaukee one key' -> 'milwaukee'
    if len(toks) >= 2:
        for cut in range(1, len(toks)):
            if LINE_PHRASE_RE.match(" ".join(toks[cut:])):
                toks = toks[:cut]; break
    return " ".join(toks)

def brand_key(name):
    """'ключ бренда': ядро без моделей/юрлиц/шума/скобок.
       makita (germany) / makita werkzeug / makita 40v max -> 'makita'.
       Ключ всегда в одной письменности: кириллический токен, транслит-совпадающий
       с латинским токеном ядра, заменяется им ('макита lxt' -> 'makita', а не
       'makita макита'), иначе ключ ломается на два и канон теряется."""
    core = core_tokens(strip_line_tail(name))
    out = []
    for t in core:
        replaced = False
        if has_cyr(t) and not has_lat(t):
            tt = translit_cyr_to_lat(t)
            for o in out:
                oo = translit_cyr_to_lat(o) if has_cyr(o) else o
                if len(tt) >= 4 and (tt == oo or lev(tt, oo) <= 2):
                    replaced = True; break
            if not replaced and len(t) >= 4:
                # фонетическая сверка русской записи с латинским токеном ядра:
                # 'делтал'~dewalt, 'милуоки'~milwaukee — дырявый транслит это не ловит
                for o in out:
                    if has_lat(o) and not has_cyr(o) and len(o) >= 5 and phon_match(t, o):
                        replaced = True; break
        elif has_lat(t) and not has_cyr(t):
            for o in out:
                if has_cyr(o) and not has_lat(o):
                    oo = translit_cyr_to_lat(o)
                    if len(t) >= 4 and (t == oo or lev(t, oo) <= 2):
                        replaced = True; break
                    elif len(o) >= 4 and len(t) >= 5 and phon_match(o, t):
                        replaced = True; break
        if not replaced:
            out.append(t)
    return " ".join(out)

CYR2LAT_FULL = str.maketrans({
    "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ж":"zh","з":"z","и":"i","й":"y",
    "к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u",
    "ф":"f","х":"h","ц":"ts","ч":"ch","ш":"sh","щ":"sch","ъ":"","ь":"","э":"e","ю":"yu","я":"ya"})

def translit_cyr_to_lat(cyr_word):
    """транслит кириллического слова в латиницу для сверки с брендом (makita==макита).
       допускает основные варианты: kh/h, c/s, u/v — сравнивающий lev это терпит."""
    return cyr_word.translate(CYR2LAT_FULL)

def prefix_variant(big, small):
    """small — 'ядро' big, лишние токены big — только шум/гео/юрлицо/модель.
       devolt промышленный -> devolt ; bosch auto parts -> bosch"""
    cb, cs = core_tokens(big), core_tokens(small)
    if not cs or len(cs) >= len(cb): return False
    if cb[0] != cs[0] and not typo_match(cb[0], cs[0]): return False
    st = set(tokens(small))
    rest = [t for t in tokens(strip_brackets(big)) if t not in st]
    return bool(rest) and all(NOISE_RE.match(t) or t in LEGAL or DOMAIN_RE.search(t)
                              or MODEL_TOKEN_RE.match(t) or t in CORP_WORDS
                              for t in rest)

def reduce_to_core(name, keep_short=False):
    """схлопнуть вариант к базовому имени: отбросить модельные/линеечные/корпоративные
       хвосты. 'makita 40v max' -> 'makita'; 'bosch power tools' -> 'bosch';
       'hilti nuron' -> 'hilti'; 'макита лхт' -> 'макита'.

       keep_short=True: короткие слова (bl, pro, xr) НЕ вырезаются из середины —
       иначе 'makita bl' превращается в мусор; для них срабатывает только правило
       хвоста. Используется на этапе сбора алиасов, чтобы не терять формы вроде
       'makita bl', которые затем схлопнутся к 'makita' через хвост/ключ."""
    toks = tokens(strip_brackets(name))
    if not toks: return ""
    while len(toks) > 1 and (LINE_WORDS_RE.match(toks[-1]) or MODEL_TOKEN_RE.match(toks[-1])
                             or toks[-1] in CORP_WORDS or toks[-1] in TAIL_STOP):
        toks.pop()
    # фразовая линейка целиком ('milwaukee one key' -> 'milwaukee')
    if len(toks) >= 2:
        for cut in range(1, len(toks)):
            if LINE_PHRASE_RE.match(" ".join(toks[cut:])):
                toks = toks[:cut]; break
    kept = []
    for i, t in enumerate(toks):
        is_tail_rule = (LINE_WORDS_RE.match(t) or MODEL_TOKEN_RE.match(t) or
                        t in CORP_WORDS or is_legal_tok(t))
        if is_tail_rule and not (keep_short and len(t) <= 3):
            continue
        # смешанный токен ('м18милуоки', 'makitaмакита'): если после удаления
        # кириллицы остаётся известное написание бренда — это модель+бренд, режем
        if has_cyr(t) and has_lat(t):
            stripped = re.sub(r"[а-яё]+", "", t)
            if len(stripped) >= 4 and stripped in LAT_KNOWN:
                continue
        kept.append(t)
    return " ".join(kept) if kept else toks[0]

# ------------------------------------------------------------ популярность бренда
#
# Файл собран так, что у РЕАЛЬНО популярных брендов много вариантов написания
# (латиница + кириллица + опечатки + ё-формы) — это прокси метрики popularity
# с маркетплейсов. У мусорных строк обычно 1-2 варианта.
# Дополнительно: якорный список гарантированно известных B2B/потребительских брендов.

ANCHORS_RAW = """
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
deboj дебой skil сбил black decker stanley стэнли irwin орвин general purpose grundy
gembird dexp electrolux электролюкс beko indesit hotpoint whirlpool gorenje горение
vertum вертум edon champion huter хутер carver daewoo дэу palisad палисад
sto ceresit церезит weber optibelt dayco contitech gates goodyear michelin
continental nokian nordman yokohama bridgestone pirelli ngk denso filtron mahle mann
fram victor reinz elring trw zimmermann ferodo swag febil lestat patron starline
weidmuller phoenix wago finder omron mitsubishi schmersal moeller hager
heidenhain fanuc kemppi abicor binzel twintec esab hypertherm forch friul airpress fiocchi
""".split()
ANCHORS = set(map(norm, ANCHORS_RAW))
for _w in ANCHORS_RAW:
    _n = norm(_w)
    if has_lat(_n) and not has_cyr(_n):
        LAT_KNOWN.add(_n)
        for _t in tokens(_n):
            if len(_t) >= 4: LAT_KNOWN.add(_t)

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

def canonical_rank(n):
    """Ранг каноничности имени (меньше = каноничнее). Общий для выбора головы
       слияния и для порядка в ES-строке: якорный список > латинская оригинальная
       форма (для иностранных) / кириллица (для русских) > короче и без опечаток."""
    core = brand_key(n) or n
    exact_anchor = core in ANCHORS or n in ANCHORS
    is_lat = has_lat(core) and not has_cyr(core)
    is_cyr = has_cyr(core) and not has_lat(core)
    # смешанные формы ('makita макита', 'dolmar долмар'): канон — тот токен,
    # который есть в известных написаниях брендов; при равенстве — латиница
    mixed = has_lat(core) and has_cyr(core)
    if mixed:
        lat_pref = any(t in LAT_KNOWN for t in tokens(core) if has_lat(t))
        script_pref = 0 if lat_pref else 1
    else:
        ru_brand = any(tok in ANCHORS for tok in tokens(core)) and is_cyr
        script_pref = 0 if (ru_brand and is_cyr) or (is_lat and not ru_brand) else 1
    return (
        0 if exact_anchor else 1,
        script_pref,
        len(core),
        len(n),
        n,
    )

def canonical_first(names):
    """Список имён, где ПЕРВЫМ — наиболее каноничное название бренда."""
    return sorted(set(names), key=canonical_rank)

# Поддомены известных брендов: 'bosch rexroth' — отдельный бренд (Rexroth),
# а не вариант написания 'bosch'. В одной ES-строке их быть не должно.
BRAND_SUBS = set(map(norm, """
rexroth bose siemens vodafone metabo hilti makita dewalt stanley blackdecker
husqvarna karcher danfoss grundfos wilo festo smc camozzi parker schneider legrand
weidmuller phoenixcontact wago omron fanuc kemppi esab hypertherm linde koike
milwaukee ryobi celikta stihl vidmar grifo zeus electrolux beko aeg bosch thermotechnik
buderus junkers vaillant viessmann protherm baxi ariston immergas navien
hitachi daewoo hyundai kia toyota nissan honda ford man scania iveco daf
caterpillar komatsu liebherr jcb terex bobcat case johndeere newholland
samsung lg xiaomi huawei honor realme oneplus meizu lenovo asus acer msi apple
philips dyson braun panasonic toshiba canon epson sony jbl kenwood pioneer
dexp dns harper telent starwind bbk igel tomahawk hydrat shtrio tebe
interskol enkor resanta denzel zubr elitech kalashnikov hamilton promtool
metalmaster sodem komtek cormatey geleez piella aquario neftter masters gileks
gembird oklicke Svenism erdnemo powercase speedline arrowmax forcegroove
""".split()))

# Дырявый CYR2LAT_FULL даёт 'хилти'->'hilti' (lev 1 от hilti) — ложные sub-бренды.
# Для сверки sub-доменов используем полную транслит-таблицу TR и точное совпадение.
def _tr_exact(w):
    return "".join(TR.get(c, c) for c in w)

# --- фонетическая транслит-сверка кириллического написания с латинским брендом:
# 'милуоки'~milwaukee, 'хилте'~hilti. Дырявый побуквенный транслит (х->h, ц->ts...)
# не ловит русскую запись иностранных брендов, поэтому сравниваем по «звучанию».
# Ключевое ограничение: согласные остаются на СВОИХ позициях (гласные удаляются) —
# иначе 'бош'(bsh) превращается в 'bosch'(bkhk) и матчит любой паттерн CVCVC.
TR_SINGLE = str.maketrans({
    "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ж":"j","з":"z","и":"i",
    "й":"i","к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s",
    "т":"t","у":"u","ф":"f","х":"h","ц":"c","ч":"c","ш":"s","щ":"s","ъ":"",
    "ь":"", "э":"e","ю":"u","я":"a",
})
VOWELS_C = "aeiouyj"          # 'j' = ж; 'u' здесь также из ю/у — убираем гласные
VOWELS_L = "aeiouy"

def _phon_cyr(w):
    s = w.translate(TR_SINGLE)
    return "".join(ch for ch in s if ch not in VOWELS_C)

def _phon_lat(w):
    s = re.sub(r"[^a-z]", "", w)
    s = s.replace("tch","c").replace("sch","s").replace("ck","k")
    return "".join(ch for ch in s if ch not in VOWELS_L)

_phon_cache = {}
def phon_match(cyr_word, lat_word):
    """фонетическое совпадение русской записи бренда с латинской: одинаковый
       скелет согласных (±1 правка) и сопоставимая длина"""
    key = (cyr_word, lat_word)
    r = _phon_cache.get(key)
    if r is None:
        a, b = _phon_cyr(cyr_word), _phon_lat(lat_word)
        r = (len(a) >= 3 and len(b) >= 3 and abs(len(a) - len(b)) <= 2
             and lev(a, b) <= max(1, round(max(len(a), len(b)) / 5)))
        _phon_cache[key] = r
    return r

def _brand_forms(name):
    """пары (кириллический токен, латинский токен) из ядра имени; добавляем и
       склеенную форму многословных: 'сен гобен' -> 'sengobain' ('сенгобен')."""
    out = []
    tk = core_tokens(name)
    for i in range(0, len(tk)-1):
        a, b = tk[i], tk[i+1]
        if has_cyr(a) and not has_lat(a) and has_lat(b) and not has_cyr(b):
            out.append((a, b)); out.append((b, a))
    joined_cyr = "".join(t for t in tk if has_cyr(t) and not has_lat(t))
    joined_lat = "".join(t for t in tk if has_lat(t) and not has_cyr(t))
    if len(joined_cyr) >= 4 and len(joined_lat) >= 5:
        out.append((joined_cyr, joined_lat))
    return out

def script_merge(v, groups):
    """сопоставить вариант с группой по смешанной письменности ('makita макита'):
       если в группе есть форма той же письменности, фонетически совпадающая с
       токеном v того же скрипта — это тот же бренд."""
    if has_cyr(v) == has_lat(v):
        return None
    vtoks = [t for t in tokens(v) if (has_cyr(t) if has_cyr(v) else has_lat(t))]
    if not vtoks: return None
    for gname, members in groups:
        for m in members:
            for cyw, latw in _brand_forms(m):
                probe = cyw if has_cyr(v) else latw
                if any(phon_match(probe, t) or probe == t for t in vtoks):
                    return gname
    return None

def _contains_seq(ht, ot):
    """токены ot встречаются в ht подряд: точно, опечаткой или точным транслитом"""
    def eq(a, b):
        if a == b: return True
        if has_cyr(a) != has_cyr(b):
            cy, lt = (a, b) if has_cyr(a) else (b, a)
            if _tr_exact(cy) == lt: return True
            return False
        if len(a) >= 5 and len(b) >= 5 and typo_match(a, b): return True
        return False
    if not ot or len(ot) > len(ht): return False
    for i in range(len(ht) - len(ot) + 1):
        if all(eq(x, y) for x, y in zip(ht[i:i+len(ot)], ot)):
            return True
    return False

def split_subbrands(row):
    """Из строки синонимов вычленить группы дочерних брендов/поддоменов:
       ['bosch','bosch rexroth','bosch home comfort'] -> [[bosch],[rexroth],[home comfort]]
       Только для строк с якорным головой; одиночные формы уходят в removed как noise."""
    row = list(row)
    if len(row) < 3: return [row], []
    head = min(row, key=canonical_rank)
    if canonical_rank(head)[0] != 0:      # не якорный бренд — не трогаем
        return [row], []
    ht_all = [tokens(r) for r in row]
    subs = []
    for s in BRAND_SUBS:
        st = tokens(s)
        if st == tokens(head): continue
        hit = sum(1 for t in ht_all if _contains_seq(t, st))
        if hit >= 2: subs.append((len(st), -hit, s))
    if not subs: return [row], []
    subs.sort(reverse=True)               # длинные специфичные раньше коротких
    assigned = {}; used_subs = set()
    for idx, r in enumerate(row):
        if r == head: continue
        for _, _, s in subs:
            if _contains_seq(ht_all[idx], tokens(s)):
                assigned[r] = s; used_subs.add(s); break
    groups = defaultdict(list)
    leftovers = [r for r in row if r not in assigned]
    for r, s in assigned.items(): groups[s].append(r)
    out_rows = [leftovers] if len(leftovers) >= 2 else []
    noise = leftovers if len(leftovers) < 2 else []
    for s, members in groups.items():
        cand = [s] + members if not any(m == s or typo_match(m, s) for m in members) else members
        if len(cand) >= 2:
            out_rows.append(cand)
        else:
            noise.extend(cand)
    if not out_rows: return [row], []
    return out_rows, noise

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

    # ---- индекс popularity: сколько раз вариант написания встречается в файле.
    # У популярных брендов много строк-вариантов (makita — в десятках строк),
    # у мусора — 1-2. Это прокси статистики маркетплейсов.
    pop = Counter()
    for vs in rows:
        for v in set(vs):
            pop[v] += 1
            k = brand_key(v)
            if k: pop[k] += 1

    dsu = DSU()
    for vs in rows:
        for v in vs[1:]:
            dsu.union(vs[0], v)

    # вторая волна: склейка строк по «ключу бренда» (ядро без юрлиц/шума/моделей/скобок),
    # чтобы "makita" + "makita (germany)" + "makita 40v max" + "макита" оказались в одной группе.
    # Ограничения против цепочечной склейки («samsung galaxy watch» -> «apple galaxy watch»):
    #  - ключ не короче MIN_KEY_LEN;
    #  - через один ключ проходит максимум MAX_LINKS склеек, и только если обе стороны
    #    достаточно частотны (pop >= POP_MIN) — мусорные омонимы не сцепляют ничего.
    key_owner = {}
    key_link_cnt = Counter()
    def keys_of(v):
        out = []
        k = brand_key(v)
        tk = tokens(k)
        if len(tk) >= 2:
            cand = " ".join(tk[:2])
            if len(cand) >= MIN_KEY_LEN: out.append(cand)
        elif len(tk) == 1 and len(tk[0]) >= MIN_KEY_LEN:
            out.append(tk[0])          # и латиница, и кириллица: makita / макита
        return out
    for vs in rows:
        for v in vs:
            if pop[v] < POP_MIN and not any(w in ANCHORS for w in tokens(v)):
                continue               # редкий вариант не имеет права сцеплять группы
            for k in keys_of(v):
                if pop.get(k, 0) < POP_MIN and k not in ANCHORS:
                    continue
                o = key_owner.get(k)
                if o is None:
                    key_owner[k] = v
                elif key_link_cnt[k] < MAX_LINKS:
                    dsu.union(o, v)
                    key_link_cnt[k] += 1

    groups = defaultdict(set)
    for vs in rows:
        for v in vs:
            groups[dsu.find(v)].add(v)

    # волна 3: склейка по смешанной письменности ('makita макита' <-> 'милуоки',
    # 'делтал dewalt' <-> 'dewalt'): кириллический токен варианта v фонетически
    # совпадает с кириллическим токеном группы (или наоборот — латинский).
    # Только частотные варианты и только якорные группы — защита от ложных омофонов.
    def _tok_match(t, probe):
        """t и probe — односкриптовые; совпадают точно или фонетически
           (для кириллицы probe может быть латинским словом бренда)"""
        if t == probe: return True
        if has_cyr(t) and has_lat(probe): return phon_match(t, probe)
        if has_lat(t) and has_cyr(probe): return phon_match(probe, t)
        return False

    anchor_set = set()
    for root, g in groups.items():
        if any(any(w in ANCHORS for w in tokens(m)) or m in ANCHORS for m in g):
            anchor_set.add(root)
    group_toks = {root: {t for m in g for t in tokens(brand_key(m) or m)}
                  for root, g in groups.items()}
    merged_into = {}
    for vs in rows:
        for v in vs:
            if pop[v] < POP_MIN: continue
            if has_cyr(v) == has_lat(v): continue      # не смешанная письменность
            r1 = dsu.find(v)
            if r1 not in anchor_set: continue
            vt = [t for t in tokens(v)]
            target = None
            for r2 in anchor_set:
                if r2 == r1: continue
                toks2 = group_toks.get(r2)
                if not toks2: continue
                if all(any(_tok_match(t, p) for p in toks2)
                       for t in vt if (has_cyr(t) != has_lat(t))):
                    target = r2; break
            if target is not None:
                dsu.union(target, v)
                merged_into.setdefault(target, set()).update(groups[r1])
                anchor_set.discard(r1)
                group_toks[target] |= group_toks.pop(r1, set()) | {t for m in groups[r1] for t in tokens(brand_key(m) or m)}
    for root, extra in merged_into.items():
        groups[root] |= extra
    absorbed = set().union(*merged_into.values()) if merged_into else set()
    groups = {r: g for r, g in groups.items() if not (g & absorbed)}

    # гигантские группы — артефакты склейки; разбираем их по «ключу бренда»,
    # но НЕ режем группу на куски, если после разбивки остаётся слишком много
    # маленьких подгрупп (это означало бы потерю синонимов кириллицы и опечаток).
    giant_split = 0
    fixed_groups = []
    for g in list(groups.values()):
        if len(g) <= MAX_GROUP:
            fixed_groups.append(g); continue
        sub = defaultdict(set)
        for v in g:
            sub[brand_key(v) or "_"].add(v)
        big_subs = [s for s in sub.values() if len(s) > 1]
        if len(big_subs) < max(3, len(g) // 4):
            # группа склеена legitimately (много одиночек-вариантов одного ядра) — оставляем как есть
            fixed_groups.append(g); continue
        giant_split += 1
        for members in sub.values():
            if len(members) > MAX_GROUP:      # всё ещё слишком много — режем по первому токену
                sub2 = defaultdict(list)
                for v in members:
                    tk = tokens(brand_key(v))
                    sub2[tk[0] if tk else "_"].append(v)
                for mm in sub2.values():
                    if len(mm) > MAX_GROUP:
                        for v in mm: fixed_groups.append({v})
                    else:
                        fixed_groups.append(set(mm))
            else:
                fixed_groups.append(set(members))
    groups = {id(g): g for g in fixed_groups}
    print(f"[i] исходных строк: {len(rows)}, групп после union-find: {len(groups)} "
          f"(разбито гигантских: {giant_split})", file=sys.stderr)

    removed = []          # (reason, item)
    final_rows = []

    for g in groups.values():
        orig_first = sorted(g)[0]
        # 1) строки-«не бренд» целиком
        # Точечная защита от ложных склеек якорных брендов (makita+dolmar):
        # удаляем из группы только варианты, чьё ядро — ДРУГОЙ известный латинский
        # бренд; кириллица/опечатки того же бренда при этом не трогаются.
        def _brand_core_lat(v):
            b = reduce_to_core(strip_noise(v))
            tk = tokens(b)
            c = tk[0] if tk else ""
            return c if (c and has_lat(c) and len(c) >= 5 and c in ANCHORS) else None
        lat_cores = Counter(cc for cc in (_brand_core_lat(v) for v in g) if cc)
        if len(lat_cores) >= 2:
            main = max(lat_cores, key=lambda c: (lat_cores[c], pop.get(c, 0)))
            g = {v for v in g if _brand_core_lat(v) in (main, None)}
        joined = ",".join(sorted(g))
        if is_bad_line(joined) or any(is_bad_line(v) for v in g):
            bad = [v for v in g if is_bad_line("," + v)]
            if len(bad) >= max(1, len(g)//2):
                removed.append(("not_a_brand", orig_first)); continue

        # 2) ЧИСТКА БЕЗ ПОТЕРЬ: для каждого исходного варианта храним пару
        #    (полная очищенная форма full, базовое имя base). Полная форма сохраняет
        #    кириллицу и опечатки (макита, макитка), base нужен только для склейки
        #    ('makita 40v max' -> 'makita'). Раньше вывод шёл только из base — и все
        #    русские формы терялись. Теперь в строку попадают обе.
        pairs = []
        for v in g:
            full = strip_noise(v)
            if not full or looks_like_garbage(full): continue
            base = reduce_to_core(full)
            if not base or looks_like_garbage(base): base = full
            pairs.append((full, base))
        if not pairs:
            removed.append(("garbage_after_clean", orig_first)); continue

        # 3) слияние вариантов, НЕ меняющих базовое слово: одинаковый ключ бренда,
        #    приставочные формы, транслит, опечатки одного слова.
        pairs.sort(key=lambda p: (-len(core_tokens(p[1])), len(p[1]), len(p[0])))
        merged = []            # [(head_full, head_base, [aliased pairs...])]
        for item in pairs:
            n, nk = item
            placed = False
            for slot in merged:
                m, mk, aliases = slot
                if (nk and mk and nk == mk) or prefix_variant(m, n) or prefix_variant(n, m) \
                   or same_translit(m, n) or \
                   (len(tokens(m)) == len(tokens(n)) and typo_match(m, n)) or \
                   (tokens(m)[0] == tokens(n)[0] and len(tokens(n)) == 1 and len(tokens(m)) > 1):
                    aliases.append(item)
                    # голова — более каноничная форма (якорь/латиница/короче)
                    cand = min([m] + [a[0] for a in aliases], key=canonical_rank)
                    cb = next((ab for af, ab in [item] + aliases if af == cand), nk)
                    slot[0], slot[1] = cand, cb
                    placed = True
                    break
            if not placed:
                merged.append([n, nk, []])

        # base-формы («makita», «bosch») собираем со всех вариантов — они и есть
        # канонические короткие имена; полные формы добавляем как алиасы.
        bases_all = {b for _, b in pairs if b}
        heads = [m for m, _, _ in merged]
        merged_names = list(dict.fromkeys(heads + sorted(bases_all, key=len)))

        # 3b) ФИНАЛЬНАЯ КОНВЕРГЕНЦИЯ: все варианты с одинаковым «ключом бренда»
        #     (ядро без юрлиц/шума/моделей/скобок) — это один бренд, и в строке ES
        #     должны остаться только базовые формы: 'makita', 'makita werkzeug',
        #     'makita 40v max' -> 'makita'; 'макита хгт' -> 'макита'.
        #     Для каждого ядра держим: латинскую канон-форму, кириллическую канон-форму
        #     и частотные однословные опечатки того же корня (maketra, макитка...).
        by_key = defaultdict(list)
        loose = []
        for n in merged_names:
            k = brand_key(n)
            if not k:
                # полностью модельное имя ('te hilti', 'dewalt dw') после резки ключа
                # схлопывается к первому токену — иначе остаётся мусорным «loose»-хвостом
                tk0 = tokens(strip_brackets(n))
                if len(tk0) > 1 and MODEL_TOKEN_RE.match(tk0[0]):
                    k = " ".join(tk0[1:])
            # склеенный смешанный токен ('м18милуоки'): вычленяем известное написание
            # бренда из середины — иначе форма повиснет отдельным «loose»-мусором
            if not k and len(tokens(n)) == 1:
                t = tokens(n)[0]
                if has_cyr(t) and has_lat(t):
                    best = ""
                    for cand in LAT_KNOWN:
                        if len(cand) >= 5 and cand in t and len(cand) > len(best):
                            best = cand
                    if best: k = best
            if k: by_key[k].append(n)
            else: loose.append(n)
        converged = []
        for k, forms in by_key.items():
            ktoks = tokens(k)
            keep = {k}
            # известные написания ядра (латиница/кириллица из ANCHORS): 'dolmar', 'долмар'
            known_forms = set()
            for a in ANCHORS:
                at = tokens(a)
                if len(at) == 1 and len(ktoks) == 1 and typo_match(at[0], ktoks[0]):
                    known_forms.add(a)
            for f in forms:
                ft = tokens(f)
                # форма «того же слова»: токен за токеном равны или отличаются опечаткой
                same_shape = (len(ft) == len(ktoks) and
                              all(a == b or typo_match(a, b) for a, b in zip(ft, ktoks)))
                if not same_shape: continue
                if any(LINE_WORDS_RE.match(t) or MODEL_TOKEN_RE.match(t) for t in ft): continue
                # чистая кириллица (макита) или чистая латиница (makita) — канон;
                # смешанная письменность ('dolmar долмар') — канон, если все токены
                # совпадают с известными написаниями ядра; опечаточные — только однословные
                pure_cyr = has_cyr(f) and not has_lat(f)
                pure_lat = has_lat(f) and not has_cyr(f)
                known_mixed = (not pure_cyr and not pure_lat and
                               all(t in known_forms or t in ktoks for t in ft))
                if pure_cyr or pure_lat or known_mixed or len(ft) == 1:
                    keep.add(f)
            # ограничиваем: сначала канонические (лат+кир+известные смешанные), затем топ по частоте опечатки
            ordered = sorted(keep, key=lambda x: (x != k, -pop.get(x, 0), len(x)))
            canon_forms = [x for x in ordered if x == k or
                           ((has_cyr(x) and not has_lat(x)) or (has_lat(x) and not has_cyr(x)) or
                            all(t in known_forms or t in ktoks for t in tokens(x)))]
            typos = [x for x in ordered if x not in canon_forms]
            converged.extend((canon_forms + typos)[:MAX_FORMS_PER_KEY])
        # loose-формы без ключа бренда: если их ядро совпадает с ядром канона из
        # converged (после резки хвостов), они не нужны — схлопываются в канон.
        conv_cores = {" ".join(core_tokens(c)) for c in converged}
        def core_of(n):
            ct = core_tokens(n)
            return " ".join(ct) if ct else n
        loose = [n for n in loose if core_of(n) not in conv_cores]
        merged_names = list(dict.fromkeys(converged + loose))

        # 4) релевантность/популярность + тематический фильтр (B2B-инструменты/оборудование)
        n_cores, _ = popularity_score(merged_names)
        gpop = max(pop[v] for v in g) if g else 0     # макс. частота варианта в группе
        anchor = any(any(w in ANCHORS for w in tokens(m)) or m in ANCHORS for m in merged_names)
        protected = any(m in ANCHOR_PROTECT or any(w in ANCHOR_PROTECT for w in tokens(m))
                        for m in merged_names)
        first_tok = tokens(merged_names[0])[0] if merged_names else ""
        stop = first_tok in STOP_FIRST_TOKENS
        # тема вне сегмента: ≥2 разных topic-слова в группе или ≥1 в маленькой группе
        twords = set()
        for m in merged_names:
            mtoks = set(tokens(m))
            for w in TOPIC_WORDS:
                if " " in w:
                    if w in m: twords.add(w.split()[0])
                elif w in mtoks: twords.add(w)
        off_topic = len(twords) >= 2 or (len(twords) >= 1 and len(merged_names) <= 3)
        frequent = gpop >= POP_MIN or any(pop.get(k, 0) >= POP_MIN
                              for k in (brand_key(m) for m in merged_names))
        keep = anchor or protected or (not stop and not off_topic and (n_cores >= 3 or frequent))
        if not keep:
            reason = ("stopped_topic" if stop else
                      "off_topic" if off_topic else "low_popularity")
            removed.append((reason, " | ".join(merged_names))); continue

        # 5) финальная форма строки: канон первым, без подстрок и мусора
        row = canonical_first(merged_names)
        row = [r for r in row if r and ">" not in r and "(" not in r and ")" not in r
               and not looks_like_garbage(r)]
        # ES synonyms: «makita» и «makita xgt» в одной строке бессмысленны —
        # подстроки удаляем, оставляем независимые формы.
        # Порядок важен: сначала длинные (они же якорные), потом короткие;
        # короткая форма («makita») никогда не удалит канон, а вот «makita lxt»
        # при длинном каноне («bosch professional 18v») останется как осмысленный синоним.
        row.sort(key=lambda r: (-len(r), canonical_rank(r)))
        pruned = []
        for r in row:
            if any(r != p and r in p for p in pruned): continue
            pruned.append(r)
        row = canonical_first(pruned)
        if len(row) < 2:
            removed.append(("single_form_left", row[0] if row else orig_first)); continue
        # 6) разъединение дочерних брендов/поддоменов ДО усечения MAX_SYNS —
        #    иначе хвостовые поддомены ('bosch rexroth') терялись без своей группы.
        for sub in split_subbrands(row)[0]:
            sub = canonical_first(sub)
            if len(sub) >= 2:
                final_rows.append(sub[:MAX_SYNS])

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
