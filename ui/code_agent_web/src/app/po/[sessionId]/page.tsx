'use client';

import { useParams } from 'next/navigation';
import POSessionPage from '@/components/POSessionPage';

export default function POSessionRoute() {
  const params = useParams();
  const sessionId = params.sessionId as string;
  return <POSessionPage sessionId={sessionId} />;
}
