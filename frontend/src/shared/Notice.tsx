import { AlertCircle } from 'lucide-react';
import { errorMessage } from '../api/client';

export function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="notice error" role="alert">
      <AlertCircle size={16} />
      <span>{errorMessage(error)}</span>
    </div>
  );
}

export function EmptyState({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-mark">—</div>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
