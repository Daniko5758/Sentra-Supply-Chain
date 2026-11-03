import { Link, useParams } from 'react-router-dom';
import { ReportPanel } from './ReportPanel';

export function CaseReportPage() {
  const { caseId } = useParams();
  if (!caseId) {
    return (
      <section className="case-report-page">
        <h1>Case report</h1>
        <p>Case not found.</p>
        <Link to="/cases">Back to cases</Link>
      </section>
    );
  }
  return <ReportPanel caseId={caseId} standalone />;
}
