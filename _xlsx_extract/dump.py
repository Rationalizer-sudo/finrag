import zipfile, re, sys, io
import xml.etree.ElementTree as ET

sys.stdout.reconfigure(encoding='utf-8')
path = r"C:\Users\28212\Desktop\校企合作\校企合作创新实践课题汇总.xlsx"
z = zipfile.ZipFile(path)
NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'

# shared strings
sst = []
root = ET.fromstring(z.read('xl/sharedStrings.xml'))
for si in root.findall(NS+'si'):
    parts = []
    for t in si.iter(NS+'t'):
        parts.append(t.text or '')
    sst.append(''.join(parts))

# workbook sheet names
wb = ET.fromstring(z.read('xl/workbook.xml'))
for s in wb.iter(NS+'sheet'):
    print('SHEET:', s.get('name'))
print('=' * 80)

sheet = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
def colnum(ref):
    m = re.match(r'([A-Z]+)(\d+)', ref)
    c, r = m.group(1), int(m.group(2))
    n = 0
    for ch in c:
        n = n*26 + (ord(ch)-64)
    return n, r

rows = {}
for row in sheet.iter(NS+'row'):
    for c in row.findall(NS+'c'):
        ref = c.get('r')
        cn, rn = colnum(ref)
        t = c.get('t')
        v = c.find(NS+'v')
        isel = c.find(NS+'is')
        val = ''
        if t == 's' and v is not None:
            val = sst[int(v.text)]
        elif t == 'inlineStr' and isel is not None:
            val = ''.join(x.text or '' for x in isel.iter(NS+'t'))
        elif v is not None:
            val = v.text
        if val is None:
            val = ''
        val = val.strip()
        if val:
            rows.setdefault(rn, {})[cn] = val

for rn in sorted(rows):
    for cn in sorted(rows[rn]):
        print(f"[R{rn}C{cn}] {rows[rn][cn]}")
    print('-' * 70)
