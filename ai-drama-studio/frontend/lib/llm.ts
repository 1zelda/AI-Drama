import { loadSettings } from './settings';

const SYSTEM_PROMPT = 'You are an expert AI drama director and scriptwriter. You help users plan AI-generated short drama series. Your expertise includes: story structure, character development, visual direction, and episode planning. Always respond in the same language as the user. Be creative, detailed, and practical.';

async function callLLM(messages: any[], opts: any = {}) {
  const s = loadSettings();
  const apiKey = s.api_key;
  const baseUrl = s.base_url;
  const model = s.model || 'deepseek-chat';
  if (!apiKey) throw new Error('API key not configured. Please set it in Settings.');
  const res = await fetch(baseUrl + '/chat/completions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + apiKey },
    body: JSON.stringify({ model, messages, ...opts }),
  });
  if (!res.ok) throw new Error('LLM error: ' + res.status + ' ' + await res.text());
  const data = await res.json();
  return data.choices[0].message.content;
}

export async function planDrama(title: string, description: string = '', existingContext: any = null): Promise<any> {
  const msgs: any[] = [{ role: 'system', content: SYSTEM_PROMPT }];
  if (existingContext) msgs.push({ role: 'user', content: 'Current context: ' + JSON.stringify(existingContext) });
  msgs.push({ role: 'user', content: \Create a complete drama plan for: '\'\\nDescription: \\\n\\nOutput valid JSON with these fields: synopsis (one paragraph), genre, tone, total_episodes (6-12), episode_duration_seconds (30-60), characters (array: name, role, age, personality, appearance, costume_style), episodes (array: number, title, summary, key_scenes). Be detailed and creative.\ });
  const text = await callLLM(msgs, { response_format: { type: 'json_object' }, temperature: 0.7 });
  return JSON.parse(text);
}

export async function chatWithPlanner(projectContext: any, userMessage: string): Promise<{ reply: string; suggestions: string[] }> {
  const msgs: any[] = [{ role: 'system', content: SYSTEM_PROMPT }];
  if (projectContext) msgs.push({ role: 'user', content: 'Current project state:\\n' + JSON.stringify(projectContext) });
  msgs.push({ role: 'user', content: userMessage });
  const reply = await callLLM(msgs, { temperature: 0.8, max_tokens: 2000 });
  return { reply, suggestions: [] };
}

export async function generateStoryboard(episodeData: any, characters: any[]): Promise<any> {
  const msgs: any[] = [
    { role: 'system', content: 'You are a professional storyboard artist for AI video generation.' },
    { role: 'user', content: \Generate detailed storyboard shots for episode: \\\nSummary: \\\nCharacters: \\\n\\nOutput valid JSON with a \"shots\" array. Each shot has: shot_number, type (establishing/medium/close-up), camera (fixed/push-in/pan/track), description (detailed visual description for AI image generation), dialogue, duration_seconds, mood, lighting, composition.\ }
  ];
  const text = await callLLM(msgs, { response_format: { type: 'json_object' }, temperature: 0.5 });
  return JSON.parse(text);
}
