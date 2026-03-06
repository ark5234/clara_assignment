from utils.llm_client import LLMClient

print('Creating client...')
client = LLMClient(provider='ollama', model='llama3.2:1b')
print('Client ready. Calling extract_json...')

system = 'You are a test assistant. Respond with a single JSON object: {"ok": true, "msg": "hello"}.'
user = 'Please confirm by returning a JSON object.'

try:
    out = client.extract_json(system, user)
    print('Extracted JSON:', out)
except Exception as e:
    print('LLM call failed:', e)
