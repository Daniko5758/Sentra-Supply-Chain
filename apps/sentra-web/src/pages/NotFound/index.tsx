import { Link } from 'react-router-dom';
import { PlaceholderPage } from '../../components/PlaceholderPage';

export function NotFoundPage() {
  return (
    <PlaceholderPage title="Page not found" description="The page you are looking for does not exist.">
      <Link to="/">Back to Dashboard</Link>
    </PlaceholderPage>
  );
}
