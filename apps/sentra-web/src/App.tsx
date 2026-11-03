import { lazy, Suspense } from 'react';
import { Route, Routes } from 'react-router-dom';
import { AuthProvider } from './auth/AuthContext';
import { Layout } from './components/Layout';
import { RequireAuth } from './components/RequireAuth';
import { RequireFlag } from './components/RequireFlag';
import { RequireRole } from './components/RequireRole';
import { ApprovalsPage } from './pages/Approvals';
import { CaseDetailPage } from './pages/CaseDetail';
import { CaseReportPage } from './pages/CaseReport';
import { CasesPage } from './pages/Cases';
import { DashboardPage } from './pages/Dashboard';
import { ErpLogPage } from './pages/ErpLog';
import { LoginPage } from './pages/Login';
import { MeetingRoomPage } from './pages/MeetingRoom';
import { NotFoundPage } from './pages/NotFound';
import { RiskSignalsPage } from './pages/RiskSignals';
import { SchedulePage } from './pages/Schedule';
import { SupplierNetworkPage } from './pages/SupplierNetwork';
import { SupplierPortalPage } from './pages/SupplierPortal';
import { SystemStatusPage } from './pages/SystemStatus';
import { WhatIfPage } from './pages/WhatIf';
import { LandingPage } from './pages/Landing';

const RiskHeatmapPage = lazy(() => import('./pages/RiskHeatmap').then((module) => ({ default: module.RiskHeatmapPage })));
const AgentActivityPage = lazy(() => import('./pages/AgentActivity').then((module) => ({ default: module.AgentActivityPage })));

// Router disediakan pemanggil (BrowserRouter di main.tsx, MemoryRouter di tes).
export function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth />}>
          <Route element={<Layout />}>
            <Route path="dashboard" element={<DashboardPage />} />
            <Route path="schedule" element={<SchedulePage />} />
            <Route path="signals" element={<RiskSignalsPage />} />
            <Route
              path="heatmap"
              element={(
                <Suspense fallback={<p className="page-description" role="status">Loading Risk heatmap…</p>}>
                  <RiskHeatmapPage />
                </Suspense>
              )}
            />
            <Route path="network" element={<SupplierNetworkPage />} />
            <Route
              path="agent-activity"
              element={(
                <Suspense fallback={<p className="page-description" role="status">Loading Agent activity…</p>}>
                  <AgentActivityPage />
                </Suspense>
              )}
            />
            <Route path="cases" element={<CasesPage />} />
            <Route
              path="cases/:caseId/meeting-room"
              element={<RequireFlag flag="meeting_room"><MeetingRoomPage /></RequireFlag>}
            />
            <Route path="cases/:caseId" element={<CaseDetailPage />} />
            <Route path="cases/:caseId/report" element={<CaseReportPage />} />
            <Route path="supplier-portal" element={<SupplierPortalPage />} />
            <Route path="system-status" element={<SystemStatusPage />} />
            <Route path="whatif" element={<RequireFlag flag="whatif"><WhatIfPage /></RequireFlag>} />
            <Route
              path="approvals"
              element={(
                <RequireRole roles={['SCM_MANAGER']}>
                  <RequireFlag flag="erp_execution"><ApprovalsPage /></RequireFlag>
                </RequireRole>
              )}
            />
            <Route path="erp-log" element={<RequireFlag flag="erp_execution"><ErpLogPage /></RequireFlag>} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Route>
      </Routes>
    </AuthProvider>
  );
}
