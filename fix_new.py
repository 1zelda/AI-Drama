import sys
sys.stdout.reconfigure(encoding='utf-8')
c = open(r'C:\Users\Administrator\Documents\ChatGPT\AI漫剧真人剧\ai-drama-studio\frontend\app\new\page.tsx', 'r', encoding='utf-8').read()
old = 'import { ArrowLeft } from "lucide-react"'
new = 'import { ArrowLeft, Settings } from "lucide-react"'
assert old in c, "old not found"
c = c.replace(old, new)
open(r'C:\Users\Administrator\Documents\ChatGPT\AI漫剧真人剧\ai-drama-studio\frontend\app\new\page.tsx', 'w', encoding='utf-8').write(c)
print('done')
