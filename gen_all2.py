import sys
sys.stdout.reconfigure(encoding='utf-8')
import os

base = r'C:\Users\Administrator\Documents\ChatGPT\AI漫剧真人剧\ai-drama-studio\frontend'
lib_dir = os.path.join(base, 'lib')
os.makedirs(lib_dir, exist_ok=True)

# 1. env.ts
open(os.path.join(lib_dir, 'env.ts'), 'w', encoding='utf-8').write('''export function getenv(key: string): string {
  return process.env[key] || "";
}
''')

# 2. store.ts
open(os.path.join(lib_dir, 'store.ts'), 'w', encoding='utf-8').write('''import fs from "fs";
import path from "path";
import { v4 as uuid } from "uuid";

const PROJECTS_DIR = path.join(process.cwd(), "data", "projects");

export interface Project {
  id: string;
  title: string;
  description: string;
  plan?: any;
  characters?: any[];
  episodes?: any[];
  storyboard?: any[];
  assets?: any;
  status: string;
  created_at: string;
  updated_at: string;
}

function loadProject(id: string): Project | null {
  const fp = path.join(PROJECTS_DIR, id + ".json");
  if (!fs.existsSync(fp)) return null;
  return JSON.parse(fs.readFileSync(fp, "utf-8"));
}

function saveProject(p: Project) {
  p.updated_at = new Date().toISOString();
  fs.writeFileSync(path.join(PROJECTS_DIR, p.id + ".json"), JSON.stringify(p, null, 2), "utf-8");
}

export const ProjectStore = {
  create(title: string, description = "") {
    const id = uuid().slice(0, 8);
    const now = new Date().toISOString();
    const p: Project = { id, title, description, characters: [], episodes: [], storyboard: [], assets: {}, status: "planning", created_at: now, updated_at: now };
    saveProject(p);
    return p;
  },
  get(id: string) { return loadProject(id); },
  list() {
    if (!fs.existsSync(PROJECTS_DIR)) return [];
    return fs.readdirSync(PROJECTS_DIR).filter(f => f.endsWith(".json")).map(f => JSON.parse(fs.readFileSync(path.join(PROJECTS_DIR, f), "utf-8")));
  },
  update(id: string, updates: any) {
    const p = loadProject(id);
    if (!p) return null;
    Object.assign(p, updates);
    saveProject(p);
    return p;
  },
  updatePlan(id: string, plan: any) {
    const p = loadProject(id);
    if (!p) return null;
    p.plan = plan;
    p.characters = plan.characters || [];
    p.episodes = plan.episodes || [];
    p.status = "planning";
    saveProject(p);
    return p;
  },
};
''')

print("store.ts, env.ts written")
