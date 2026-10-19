import re, ast
txt = open('curated_fixes.py', encoding='utf-8').read()
i = txt.index("ADDITIONS = {")
j = txt.index("APPEND_TO_EXISTING = {")
block = txt[i:j]
src = block.rstrip().rstrip(',')
add = ast.literal_eval(src[len("ADDITIONS = "):])
print('entries:', len(add))
for probe in ['hm','lg','hp','vw','ZF','mg','ka','zl','sc','zf','aa','ab']:
    if probe in add:
        print(repr(probe), '->', add[probe][:10])
# count junk: keys that are pure 2-letter ascii sequences mapping to translit pairs
junk = [k for k,v in add.items() if len(k)<=2 and k.isascii() and k.isalpha()]
print('short ascii alpha keys:', len(junk))
