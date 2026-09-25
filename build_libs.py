import sys, os
sys.stdout.reconfigure(encoding='utf-8')
base = os.path.join(os.getcwd(), 'ai-drama-studio', 'frontend')
lib = os.path.join(base, 'lib')
api = os.path.join(base, 'app', 'api')
for d in [lib,
    os.path.join(api,'projects'),
    os.path.join(api,'projects','plan'),
    os.path.join(api,'projects','chat'),
    os.path.join(api,'projects','characters'),
    os.path.join(api,'projects','approvals'),
    os.path.join(api,'workflows'),
    os.path.join(api,'health')]:
    os.makedirs(d, exist_ok=True)
print('dirs ok')

with open(os.path.join(lib,'env.ts'),'w',encoding='utf-8') as f:
    f.write('export function getenv(key: string): string {\n  return process.env[key] || "";\n}\n')
print('env.ts ok')
