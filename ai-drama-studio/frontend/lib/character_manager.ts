import * as fs from 'fs';
import * as path from 'path';
import { randomUUID } from 'crypto';

export interface Character {
  id: string;
  name: string;
  role: string;
  description: string;
  appearance: string;
  costume: string;
  personality: string;
  reference_images: string[];
  created_at: string;
  updated_at: string;
}

export class CharacterManager {
  private chars: Character[] = [];
  private dir: string;

  constructor(projectId: string, storageDir: string = 'data') {
    this.dir = path.join(storageDir, projectId, 'characters');
    fs.mkdirSync(this.dir, { recursive: true });
    this.load();
  }

  private filePath() { return path.join(this.dir, 'characters.json'); }
  private load() {
    if (fs.existsSync(this.filePath())) {
      this.chars = JSON.parse(fs.readFileSync(this.filePath(), 'utf-8'));
    }
  }
  private save() {
    fs.writeFileSync(this.filePath(), JSON.stringify(this.chars, null, 2), 'utf-8');
  }

  add(name: string, role: string, description: string, appearance: string, costume: string, personality: string = ''): Character {
    const char: Character = {
      id: randomUUID().slice(0, 8), name, role, description, appearance, costume, personality,
      reference_images: [], created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    };
    this.chars.push(char);
    this.save();
    return char;
  }

  list(): Character[] { return [...this.chars]; }
  get(id: string): Character | null { return this.chars.find(c => c.id === id) || null; }
  update(id: string, updates: Partial<Character>): Character | null {
    const idx = this.chars.findIndex(c => c.id === id);
    if (idx === -1) return null;
    this.chars[idx] = { ...this.chars[idx], ...updates, updated_at: new Date().toISOString() };
    this.save();
    return this.chars[idx];
  }
  addReferenceImage(id: string, imagePath: string): Character | null {
    const char = this.get(id);
    if (!char) return null;
    char.reference_images.push(imagePath);
    char.updated_at = new Date().toISOString();
    this.save();
    return char;
  }
}
