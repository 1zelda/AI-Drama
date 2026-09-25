import { loadSettings } from './settings';
import * as fs from 'fs';
import * as path from 'path';
import { randomUUID } from 'crypto';

export interface WorkflowResult {
  prompt_id: string;
  outputs: Record<string, any>;
  images?: string[];
  videos?: string[];
}

export class ComfyUIClient {
  private serverUrl: string;
  private clientId: string;
  private _available: boolean | null = null;

  constructor(serverUrl?: string) {
    const s = loadSettings();
    this.serverUrl = (serverUrl || s.comfyui_url || 'http://localhost:8188').replace(/\/$/, '');
    this.clientId = randomUUID();
  }

  async isAvailable(): Promise<boolean> {
    if (this._available !== null) return this._available;
    try {
      const res = await fetch(this.serverUrl + '/system_stats', { signal: AbortSignal.timeout(3000) });
      this._available = res.ok;
    } catch { this._available = false; }
    return this._available;
  }

  async submit(workflow: any): Promise<string> {
    const w = JSON.parse(JSON.stringify(workflow));
    w.client_id = this.clientId;
    const res = await fetch(this.serverUrl + '/prompt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt: w }),
    });
    if (!res.ok) throw new Error('ComfyUI submit failed: ' + res.status);
    const data = await res.json();
    return data.prompt_id;
  }

  async poll(promptId: string, timeout = 600, interval = 5): Promise<WorkflowResult> {
    const deadline = Date.now() + timeout * 1000;
    while (Date.now() < deadline) {
      try {
        const res = await fetch(this.serverUrl + '/history/' + promptId);
        const data = await res.json();
        if (data[promptId]) {
          const entry = data[promptId];
          const outputs = entry.outputs || {};
          const images: string[] = [];
          const videos: string[] = [];
          for (const nodeOutputs of Object.values(outputs) as any[]) {
            for (const img of (nodeOutputs.images || [])) {
              images.push(img.filename);
            }
            for (const vid of (nodeOutputs.videos || [])) {
              videos.push(vid.filename);
            }
          }
          return { prompt_id: promptId, outputs, images, videos };
        }
      } catch {}
      await new Promise(r => setTimeout(r, interval * 1000));
    }
    throw new Error('ComfyUI timeout: ' + promptId);
  }

  async downloadImage(filename: string, subfolder: string = '', type: string = 'output'): Promise<Buffer> {
    const res = await fetch(this.serverUrl + '/view?filename=' + encodeURIComponent(filename) + '&subfolder=' + encodeURIComponent(subfolder) + '&type=' + type);
    if (!res.ok) throw new Error('Download failed: ' + res.status);
    return Buffer.from(await res.arrayBuffer());
  }

  async uploadImage(localPath: string, name: string): Promise<string> {
    const fileBuffer = fs.readFileSync(localPath);
    const form = new FormData();
    form.append('image', new Blob([fileBuffer], { type: 'image/png' }), name);
    const res = await fetch(this.serverUrl + '/upload/image', { method: 'POST', body: form });
    if (!res.ok) throw new Error('Upload failed: ' + res.status);
    const data = await res.json();
    return data.name;
  }

  static loadWorkflow(filePath: string): any {
    return JSON.parse(fs.readFileSync(filePath, 'utf-8'));
  }

  static patchWorkflow(workflow: any, patches: Record<string, Record<string, any>>) {
    const w = JSON.parse(JSON.stringify(workflow));
    for (const [nid, vals] of Object.entries(patches)) {
      if (!w[nid]) throw new Error('Node ' + nid + ' not found');
      for (const [k, v] of Object.entries(vals)) {
        w[nid].inputs[k] = v;
      }
    }
    return w;
  }

  static randomSeed() {
    return Math.floor(Math.random() * 0xFFFFFFFF);
  }
}
