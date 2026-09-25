import * as fs from 'fs';
import * as path from 'path';
import { randomUUID } from 'crypto';

export interface ApprovalItem {
  id: string;
  project_id: string;
  stage: string;
  item_type: string;
  item_id: string;
  title: string;
  description: string;
  assets: any[];
  status: 'pending' | 'approved' | 'rejected' | 'modified';
  feedback: string;
  created_at: string;
  approved_at?: string;
}

export class ApprovalGate {
  private items: ApprovalItem[] = [];
  private dir: string;

  constructor(projectId: string, storageDir: string = 'data') {
    this.dir = path.join(storageDir, projectId, 'approvals');
    fs.mkdirSync(this.dir, { recursive: true });
    this.load();
  }

  private filePath() { return path.join(this.dir, 'approvals.json'); }
  private load() {
    if (fs.existsSync(this.filePath())) {
      this.items = JSON.parse(fs.readFileSync(this.filePath(), 'utf-8'));
    }
  }
  private save() {
    fs.writeFileSync(this.filePath(), JSON.stringify(this.items, null, 2), 'utf-8');
  }

  create(stage: string, itemType: string, itemId: string, title: string, description: string, assets: any[] = []): ApprovalItem {
    const item: ApprovalItem = {
      id: randomUUID().slice(0, 8), project_id: '', stage, item_type: itemType, item_id: itemId,
      title, description, assets, status: 'pending', feedback: '',
      created_at: new Date().toISOString(),
    };
    this.items.push(item);
    this.save();
    return item;
  }

  getItems(stage?: string): ApprovalItem[] {
    if (stage) return this.items.filter(i => i.stage === stage);
    return [...this.items];
  }

  approve(id: string): ApprovalItem | null {
    const item = this.items.find(i => i.id === id);
    if (!item) return null;
    item.status = 'approved';
    item.approved_at = new Date().toISOString();
    this.save();
    return item;
  }

  reject(id: string, feedback: string = ''): ApprovalItem | null {
    const item = this.items.find(i => i.id === id);
    if (!item) return null;
    item.status = 'rejected';
    item.feedback = feedback;
    this.save();
    return item;
  }

  getPending(): ApprovalItem[] { return this.items.filter(i => i.status === 'pending'); }
}
