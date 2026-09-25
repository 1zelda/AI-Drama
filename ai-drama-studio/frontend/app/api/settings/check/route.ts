import { passthrough } from '@/lib/api';

export async function GET() {
  return passthrough('/api/settings/check');
}
