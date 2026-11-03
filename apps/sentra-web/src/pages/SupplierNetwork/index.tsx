import { useCases } from '../../api/hooks';
import { NetworkExplorer } from './NetworkExplorer';

export function SupplierNetworkPage() {
  const cases = useCases('open');
  return <NetworkExplorer
    caseOptions={cases.data ?? []}
    caseOptionsError={cases.isError}
    onRetryCaseOptions={() => void cases.refetch()}
  />;
}
