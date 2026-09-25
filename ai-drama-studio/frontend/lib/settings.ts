import * as fs from 'fs';
import * as path from 'path';

const SETTINGS_FILE = path.join(process.cwd(), 'data', 'settings.json');

export interface AppSettings {
  llm_provider: 'openai' | 'deepseek' | 'custom';
  api_key: string;
  base_url: string;
  model: string;
  comfyui_url: string;
  comfyui_available: boolean;
  kling_api_key: string;
  kling_api_url: string;
  seedance_api_key: string;
  veo_api_key: string;
}

export const DEFAULT_SETTINGS: AppSettings = {
  llm_provider: 'deepseek',
  api_key: '',
  base_url: 'https://api.deepseek.com/v1',
  model: 'deepseek-chat',
  comfyui_url: 'http://localhost:8188',
  comfyui_available: false,
  kling_api_key: '',
  kling_api_url: 'https://api.klingai.com/v1',
  seedance_api_key: '',
  veo_api_key: '',
};

export function loadSettings(): AppSettings {
  if (fs.existsSync(SETTINGS_FILE)) {
    try {
      const loaded = JSON.parse(fs.readFileSync(SETTINGS_FILE, 'utf-8')) as Partial<AppSettings>;
      return { ...DEFAULT_SETTINGS, ...loaded };
    } catch {}
  }
  return { ...DEFAULT_SETTINGS };
}

export function saveSettings(s: AppSettings): void {
  fs.mkdirSync(path.dirname(SETTINGS_FILE), { recursive: true });
  fs.writeFileSync(SETTINGS_FILE, JSON.stringify(s, null, 2), 'utf-8');
}

export function getVideoApiKey(provider: 'kling' | 'seedance' | 'veo'): string {
  const s = loadSettings();
  if (provider === 'kling') return s.kling_api_key;
  if (provider === 'seedance') return s.seedance_api_key;
  return s.veo_api_key;
}
