#!/usr/bin/env python3
import sys, json, subprocess

data = json.load(sys.stdin)
prompt = data.get('prompt', '')

stopwords = {'what','when','where','which','while','about','after','before','there','their','would','could','should','these','those','check','using','being','doing','going','getting','making','taking','having','looking','working','trying','think','know','want','need','have','will','with','from','that','this','into','then','than','just','also','been','were','them','some','your','does','will','tell','help'}
words = [w.lower() for w in prompt.split() if len(w) > 4 and w.lower() not in stopwords]
query = '|'.join(words[:5]) if words else ''

vault = '/Users/guido.dilauro/WORKDIR/WORK-WIKI'
results = []
if query:
    try:
        out = subprocess.run(
            ['grep', '-ril', '--include=*.md', '-E', query, vault],
            capture_output=True, text=True, timeout=5
        )
        files = [f for f in out.stdout.strip().split('\n') if f][:5]
        for f in files:
            rel = f.replace(vault + '/', '')
            try:
                snippet = subprocess.run(
                    ['grep', '-im', '2', '-E', query, f],
                    capture_output=True, text=True, timeout=3
                ).stdout.strip()[:200]
            except Exception:
                snippet = ''
            results.append(f'[[{rel}]]: {snippet}')
    except Exception:
        pass

if results:
    print(json.dumps({
        'hookSpecificOutput': {
            'hookEventName': 'UserPromptSubmit',
            'additionalContext': 'Relevant wiki notes:\n' + '\n'.join(results)
        }
    }))
