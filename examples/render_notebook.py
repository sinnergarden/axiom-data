from pathlib import Path
import argparse, base64, html, re
import nbformat
from nbclient import NotebookClient
import mistune

parser = argparse.ArgumentParser(description='Render the saved Data design Notebook, optionally executing its examples.')
parser.add_argument('--execute', action='store_true', help='Execute all cells before rendering; real tutorials use retained data without source calls.')
parser.add_argument('--notebook', default='../notebooks/researcher_tutorial.ipynb', help='Notebook filename in this directory, or an absolute path.')
args = parser.parse_args()
path = Path(args.notebook)
if not path.is_absolute():
    path = Path(__file__).parent / path
root = path.parents[2]
n=nbformat.read(path,as_version=4)
if args.execute:
    NotebookClient(n,timeout=240,kernel_name=n.metadata.get('kernelspec',{}).get('name','python3'),resources={'metadata':{'path':str(root)}}).execute()
nbformat.validate(n)
nbformat.write(n,path)
markdown=mistune.create_markdown(plugins=['table','strikethrough'])
body=[]; nav=[]; images=[]
evidence=n.metadata.get('axiom',{}).get('evidence','synthetic')
example_label='真实执行' if evidence=='real' else '假设示例'
for i,c in enumerate(n.cells):
    if c.cell_type=='markdown':
        match=re.search(r'^## (\d+)\. (.+)',c.source,re.M)
        ident=f'section-{match.group(1)}' if match else f'cell-{i}'
        if match: nav.append(f'<a href="#{ident}">{html.escape(match.group(1)+". "+match.group(2))}</a>')
        body.append(f'<section id="{ident}" class="markdown">{markdown(c.source)}</section>')
    else:
        outputs=[]
        for out in c.outputs:
            if out.output_type=='stream':
                outputs.append('<pre class="output-text">'+html.escape(out.text)+'</pre>')
            elif out.output_type in ['display_data','execute_result']:
                data=out.data
                if 'text/html' in data: outputs.append('<div class="table-scroll">'+data['text/html']+'</div>')
                elif 'image/png' in data:
                    png=data['image/png']; outputs.append(f'<img class="chart" src="data:image/png;base64,{png}" alt="数据演示图">')
                    imgpath=path.with_name(f'{path.stem}-chart-{len(images)+1}.png')
                    imgpath.write_bytes(base64.b64decode(png)); images.append(imgpath)
                elif 'text/plain' in data: outputs.append('<pre class="output-text">'+html.escape(data['text/plain'])+'</pre>')
        title=html.escape(c.metadata.get('example_title', example_label+' '+str(c.execution_count)))
        if c.metadata.get('presentation') == 'support':
            body.append('<section class="example"><details><summary>'+title+'</summary>'+
                        ''.join(outputs)+'<pre class="code">'+html.escape(c.source)+'</pre></details></section>')
        else:
            body.append('<section class="example"><div class="example-label">'+example_label+' · '+title+'</div>'+
                        ''.join(outputs)+'<details><summary>展开示例代码</summary><pre class="code">'+html.escape(c.source)+'</pre></details></section>')
style='''
html {scroll-behavior:smooth} body{margin:0;background:#f4f6fa;color:#202b3c;font:16px/1.75 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
header{background:#14283d;color:#f3f7fc;padding:24px max(26px,calc((100vw - 1120px)/2))} header b{font-size:24px} header p{margin:4px 0;color:#c5d4e6}
.layout{max-width:1450px;margin:auto;display:grid;grid-template-columns:245px minmax(0,1fr);gap:28px;padding:28px}
nav{position:sticky;top:20px;height:calc(100vh - 60px);overflow:auto} nav a{display:block;color:#354a63;text-decoration:none;padding:8px 10px;font-size:13px;border-bottom:1px solid #e0e7ef} nav a:hover{background:#e7edf5}
main{max-width:1120px;min-width:0} section{background:white;padding:24px 30px;margin:0 0 18px;border:1px solid #e1e6ed;border-radius:10px}
h1{font-size:31px;line-height:1.35} h2{font-size:23px;border-bottom:2px solid #eaf0f7;padding-bottom:12px;line-height:1.5} h3{font-size:19px} a{color:#176c9e}
table{border-collapse:collapse;width:100%;font-size:13px;margin:16px 0} th{background:#edf3fa;color:#213e59;text-align:left;font-weight:650} th,td{border-bottom:1px solid #dfe6ee;padding:10px;vertical-align:top;word-break:normal} tr:nth-child(even) td{background:#fafbfd}
pre{background:#f3f6fa;border-radius:7px;padding:16px;overflow:auto;font:12px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace} code{font-size:.9em} p code,li code{background:#edf2f7;padding:2px 4px;border-radius:3px}
.example{border-left:4px solid #328778}.example-label{font-size:12px;font-weight:700;color:#277469;letter-spacing:.07em}.output-text{max-height:660px}.table-scroll{overflow:auto}.table-scroll table{width:max-content;min-width:100%}.table-scroll td,.table-scroll th{max-width:300px}.chart{width:100%;max-width:1000px;height:auto}
details{border-top:1px solid #e4e9ef;padding-top:12px;margin-top:18px} summary{cursor:pointer;color:#577087;font-size:13px}.code{max-height:650px}
@media(max-width:980px){.layout{display:block;padding:14px}nav{position:static;height:auto;max-height:220px;margin-bottom:18px}section{padding:18px}h1{font-size:26px}}
@media print{nav,details{display:none}.layout{display:block}body{background:white}section{break-inside:auto;border:0}pre{white-space:pre-wrap}.table-scroll{overflow:visible}}
'''
is_dev=n.metadata.get('axiom',{}).get('audience')=='developer' or path.stem=='axiom_data_developer_design'
title='Axiom Data · For Quant Dev' if is_dev else 'Axiom Data · For Quant Researcher'
other=('axiom_data_design.html' if is_dev else 'axiom_data_developer_design.html') if path.parent.name=='notebooks' and path.parent.parent.name=='design' else ('researcher_tutorial.html' if is_dev else 'developer_tutorial.html')
other_label='阅读 Researcher 版' if is_dev else '阅读 Dev 版'
subtitle=html.escape(n.metadata.get('axiom',{}).get('subtitle','沪深300 / 2020 年起 / 假设数据演示'))
doc='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+title+'</title><style>'+style+'</style></head><body><header><b>'+title+'</b><p>'+subtitle+'</p><a style="color:#c5e8ff" href="'+other+'">'+other_label+'</a></header><div class="layout"><nav>'+''.join(nav)+'</nav><main>'+''.join(body)+'</main></div></body></html>'
out=path.with_suffix('.html'); out.write_text(doc)
print('Executed code cells:' if args.execute else 'Saved code cells:',sum(c.cell_type=='code' for c in n.cells))
print('Notebook:',path)
print('HTML:',out)
print('Charts:',[str(p) for p in images])
