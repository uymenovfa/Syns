import sys, time
from collections import defaultdict
t0=time.time()
lines=[l.rstrip('\n') for l in open('synonymsl.txt',encoding='utf-8') if l.strip()]
groups=[[t.strip() for t in l.split(',') if t.strip()] for l in lines]

CYR=set('\u0400\u0401\u0402\u0403\u0404\u0405\u0406\u0407\u0408\u0409\u040a\u040b\u040c\u040d\u040e\u040f'
        '\u0410\u0411\u0412\u0413\u0414\u0415\u0416\u0417\u0418\u0419\u041a\u041b\u041c\u041d\u041e\u041f'
        '\u0420\u0421\u0422\u0423\u0424\u0425\u0426\u0427\u0428\u0429\u042a\u042b\u042c\u042d\u042f\u0430'
        '\u0431\u0432\u0433\u0434\u0435\u0436\u0437\u0438\u0439\u043a\u043b\u043c\u043d\u043e\u043f'
        '\u0440\u0441\u0442\u0443\u0444\u0445\u0446\u0447\u0448\u0449\u044a\u044b\u044c\u044d\u044e\u044f'
        '\u0450\u0451\u0452\u0453\u0454\u0455\u0456\u0457\u0458\u0459\u045a\u045b\u045c\u045d\u045e\u045f')
TRANS=str.maketrans({'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'e','ж':'zh','з':'z','и':'i',
 'й':'y','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'h',
 'ц':'ts','ч':'ch','ш':'sh','щ':'sch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya'})
def norm(s):
    s=s.lower().translate(TRANS)
    out=[]
    for ch in s:
        if ch.isalnum(): out.append(ch)
    return ''.join(out)

tok_groups=defaultdict(set)   # token -> set of group idx
for i,g in enumerate(groups):
    for t in g: tok_groups[t].add(i)

# translit index: normkey -> tokens (only unique-ish)
lat_by_norm=defaultdict(list)
cyr_tokens=[]
for t,gs in tok_groups.items():
    if any(c in CYR for c in t): cyr_tokens.append(t)
    else: lat_by_norm[norm(t)].append(t)

pairs=[]
for t in cyr_tokens:
    k=norm(t)
    if len(k)<5: continue
    for o in lat_by_norm.get(k,[]):
        # different groups and no shared group
        if not (tok_groups[t] & tok_groups[o]):
            pairs.append((t,o))
print("translit cross-group candidates:",len(pairs), file=sys.stderr)
for x in sorted(pairs): print(x[0]+'\t'+x[1])
print("elapsed %.1fs"%(time.time()-t0), file=sys.stderr)
