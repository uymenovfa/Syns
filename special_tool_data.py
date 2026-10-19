# -*- coding: utf-8 -*-
"""Курационные данные категории «СПЕЦИАЛИЗИРОВАННЫЙ ИНСТРУМЕНТ»
(SPECIAL_TOOL_BRANDS).

Сегменты: электроизмерительный и диагностический инструмент, монтажный
инструмент электрика (обжим, VDE) и сантехника (резьба, гибка,
развальцовка), взрывозащищённый/искробезопасный инструмент, прочий
профессиональный специнструмент.

Формат: канонический бренд -> дополнительные формы
(русские транслитерации, разговорные/сленг, сокращённые, частотные опечатки).
Используется скриптом add_industry_categories.py.

Правила курации (продакшн):
  * никаких смешанных алфавитов (латиница+кириллица в одном токене);
  * никаких CJK-токенов; никаких общих слов-терминов («мегаомметр»,
    «mader», «vde» — только в составе уникальных связок);
  * токен уникален по normkey внутри записи и не конфликтует с чужими
    брендами (проверено против synonyms_clean.txt на момент создания);
  * бренды, уже присутствующие в словаре (Fluke, Metrawatt, Resanta,
    Rothenberger, RIDGID, Virax, Rehau, Mirka, Knipex, Phoenix Contact,
    Hilti, Greenlee), не дублируются новыми группами — им дописываются
    только новые уникальные формы под несуществующими ключами-доменами;
  * опасные гомоглифы исключены полностью: KLEIN (→KSB), UNIT (→Юнит),
    ВЕКТОР (→Vector Inc), AMPERE, ELTEKO оставлены только после проверки.
"""

SPECIAL_TOOL_BRANDS = {
    # ================= Электроизмерительный инструмент =================
    'Megger': ['megger testing', 'megger instruments', 'megger ltd',
               'meger', 'megar', 'мегер', 'мэггер', 'меггер'],
    'Seaward Electronics': ['seaward', 'seward electronics',
                            'сеуорд', 'севард', 'сеуорд электроникс'],
    'Kyoritsu': ['kyoritsu keiki', 'kioritsu', 'keritsu',
                 'кёрицу', 'корицу', 'коритсу', 'кьоритсу'],
    'GW Instek': ['gw instek group',
                  'гвинстек', 'джувинстек', 'гвинтек'],
    'Mastech': ['mastech instruments', 'mastec',
                'мастеч', 'мастек', 'мастеч инструмент'],
    'Benning': ['benning mm', 'benning pruftechnik', 'bening',
                'беннинг', 'беннинг тестер', 'беннинг мультиметр'],
    'Sonel': ['sonel sw', 'sonel glogow', 'sonell',
              'сонел', 'сонель', 'сонел измеритель'],
    'Elteko': ['eltcko', 'элтеко', 'элтеко приборы'],
    'Brymen': ['braimen', 'браймен', 'браймен мультиметр'],
    # ================= Инструмент электрика (обжим, изоляция) =============
    'Weidmüller Tools': ['weidmueller tools', 'weidmueller ommitool',
                         'vidmyuller', 'вайдемюллер', 'видмюллер',
                         'вайдмюллер'],
    'Klauke': ['klauke crimping', 'klauke pzl', 'klawke', 'clauke',
               'клауке', 'клауке пресс', 'клаука'],
    'Renner': ['renner press', 'renner crimptools',
               'реннер', 'ренер пресс'],
    'Opt Crimp': ['opt crimptools', 'опт кримп'],
    'Jokari': ['jokari speed 2000', 'jocari', 'yokari',
               'жокари', 'йокари', 'жокари ножницы'],
    'VDE insulated tools': ['изолированный инструмент',
                   'диэлектрический инструмент'],
    'Phoenix Contact Tools': ['phoenix contact tool', 'flipper phoenix',
                              'феникс контакт инструмент', 'фениксконтакт'],
    # ================= Сантехнический специнструмент =================
    'Rothenberger Tool Business': ['rotengereger', 'rothenberg',
                                   'ратенбергер', 'ротенбери',
                                   'rothenberger rvs'],
    'RIDGID Eurasia': ['ridgid idca', 'ридждид', 'ридгид',
                       'риджид трубный ключ', 'ридгид плашка'],
    'Virax Russia': ['virax tube cutter', 'вирах', 'выракс',
                     'виракс труборез'],
    'Gebr. Mader GmbH': ['gebruder mader', 'гебрюдер мадер',
                         'мадер инструмент'],
    'Rehau Tools': ['rehau raufon', 'реехау', 'рехоу',
                    'рехау фитинги'],
    'Hilti Firestop': ['хилти файрстоуп',
                       'хилти противопожарная', 'хилти монтажный пистолет'],
    # ================= Взрывозащищённый / искробезопасный =================
    'Waldmann': ['waldmann explosion proof', 'waldermann',
                 'вальдман', 'вальдман взрывозащищенный'],
    'Von Roll Tools': ['fon roll', 'фон ролль'],
    'North Sea Tool Company': ['north sea tool', 'норт си тул'],
    'Ex Tool Group': ['ex-tool', 'искробезопасный инструмент',
                      'медный инструмент взрывозащищенный'],
    # ================= Прочий специнструмент =================
    'Bessey Tools': ['bessey spanntechnik', 'bessi', 'becey',
                     'беззи', 'бесси', 'безей'],
    'Pferzkolle': ['pferzkolle zangen', 'pfferzkolle', 'пферцколь'],
    'Rennsteig Werkzeuge': ['rennsteig schleifwerkzeuge',
                             'реннштайг', 'ренштейг'],
    'Edma': ['edma abrasives', 'едма', 'эдма круги'],
    'Mirka Abrasives': ['mirka abramaster', 'mirka derosan', 'mirrora',
                      'мирка шлифование'],
}

