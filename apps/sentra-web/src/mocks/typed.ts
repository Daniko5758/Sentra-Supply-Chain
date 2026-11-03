import actionsApprove from './actions_ACT-0001_approve.json';
import actionsReject from './actions_ACT-0001_reject.json';
import actionsFailed from './actions_ACT-0002_approve.json';
import actionsRetry from './actions_ACT-0001_retry.json';
import alerts from './alerts.json';
import alertRead from './alerts_AL-0001_read.json';
import approvals from './approvals.json';
import cases from './cases.json';
import caseDetail from './cases_CASE-0001.json';
import caseClose from './cases_CASE-0001_close.json';
import caseDismiss from './cases_CASE-0001_dismiss.json';
import meetingRoom from './cases_CASE-0001_meeting-room.json';
import customDecision from './cases_CASE-0001_decision_custom-actions.json';
import customCatalog from './cases_CASE-0001_decision_custom-actions_catalog.json';
import meetingRoomResend from './cases_CASE-0001_meeting-room_resend.json';
import messages from './cases_CASE-0001_messages.json';
import messagesV2 from './cases_CASE-0001_messages_v2.json';
import approveAll from './cases_CASE-0001_options_A_approve-all.json';
import recommendations from './cases_CASE-0001_recommendations.json';
import recommendationSubmit from './cases_CASE-0001_recommendations_A_submit.json';
import recommendationsV2 from './cases_CASE-0001_recommendations_v2.json';
import report from './cases_CASE-0001_report.json';
import reportGenerate from './cases_CASE-0001_report_generate.json';
import caseTrace from './cases_CASE-0001_trace.json';
import caseDetailV2 from './cases_CASE-0001_v2.json';
import tariffCaseDetail from './cases_CASE-0002.json';
import tariffMessages from './cases_CASE-0002_messages.json';
import tariffRecommendations from './cases_CASE-0002_recommendations.json';
import case3 from './cases_CASE-0003.json';
import case3V2 from './cases_CASE-0003_v2.json';
import case3Recommendations from './cases_CASE-0003_recommendations.json';
import case3RecommendationsV2 from './cases_CASE-0003_recommendations_v2.json';
import case4 from './cases_CASE-0004.json';
import case4Recommendations from './cases_CASE-0004_recommendations.json';
import case4MeetingRoom from './cases_CASE-0004_meeting-room.json';
import case4Decision from './cases_CASE-0004_decision.json';
import case4CustomDecision from './cases_CASE-0004_decision_custom-actions.json';
import case4CustomCatalog from './cases_CASE-0004_decision_custom-actions_catalog.json';
import case5 from './cases_CASE-0005.json';
import case5Recommendations from './cases_CASE-0005_recommendations.json';
import chat from './chat.json';
import dashboard from './dashboard.json';
import demoScenarios from './demo_scenarios.json';
import demoScenariosTest from './demo_scenarios_test.json';
import demoInjectCustom from './demo_inject_custom.json';
import demoInjectFlood from './demo_inject_flood_karawang.json';
import demoInjectScenario from './demo_inject_scenario.json';
import demoInjectTariff from './demo_inject_tariff_cn.json';
import demoReset from './demo_reset.json';
import erpLog from './erp-log.json';
import health from './health.json';
import heatmap from './heatmap_countries.json';
import me from './me.json';
import meTest from './me_test.json';
import network from './network.json';
import networkAll from './network_all.json';
import tariffNetwork from './network_CASE-0002.json';
import networkV2 from './network_v2.json';
import riskScan from './risk_scan.json';
import modeScanRun from './runs_RUN-20261031-0003.json';
import run from './runs_RUN-20261031-0007.json';
import reportRun from './runs_RUN-20261031-0012.json';
import schedule from './schedule.json';
import signals from './signals.json';
import supplier from './suppliers_S01.json';
import supplierS02 from './suppliers_S02.json';
import supplierS03 from './suppliers_S03.json';
import supplierS04 from './suppliers_S04.json';
import supplierS05 from './suppliers_S05.json';
import supplierS06 from './suppliers_S06.json';
import supplierS07 from './suppliers_S07.json';
import supplierS08 from './suppliers_S08.json';
import supplierS09 from './suppliers_S09.json';
import supplierS10 from './suppliers_S10.json';
import supplierS11 from './suppliers_S11.json';
import supplierS12 from './suppliers_S12.json';
import supplierS13 from './suppliers_S13.json';
import supplierS14 from './suppliers_S14.json';
import supplierS15 from './suppliers_S15.json';
import systemChecks from './system_checks.json';
import systemStatus from './system_status.json';
import trace from './trace.json';
import whatIf from './whatif.json';
import whatIfBekasi from './whatif_bekasi.json';
import whatIfBogor from './whatif_bogor.json';
import whatIfHamamatsu from './whatif_hamamatsu.json';
import whatIfKarawang from './whatif_karawang.json';
import whatIfLocations from './whatif_locations.json';
import whatIfNingbo from './whatif_ningbo.json';
import whatIfPurwakarta from './whatif_purwakarta.json';
import whatIfRayong from './whatif_rayong.json';
import whatIfShenzhen from './whatif_shenzhen.json';
import whatIfTangerang from './whatif_tangerang.json';
import type {
  ActionResult,
  AgentRun,
  AgentTrace,
  Alert,
  ApprovalItem,
  CaseDetail,
  CaseReport,
  CaseSummary,
  ChatResponse,
  CustomActionCatalog,
  CustomDecisionResult,
  Dashboard,
  Decision,
  DemoInjectResponse,
  DemoResetResponse,
  ErpChangeDocument,
  Health,
  HeatmapCountries,
  Me,
  MeetingNotification,
  MeetingRoom,
  Network,
  Recommendations,
  RiskScanResponse,
  Schedule,
  Signal,
  SupplierMessage,
  SupplierProfile,
  SystemStatus,
  ScenarioList,
  WhatIfResult,
  RunQueuedResponse,
  SubmittedRecommendation,
} from '../api/types';

// JSON imports widen string literals to `string`. This mapped type keeps the
// structural checks (required keys and primitive value kinds) while tests check
// the contract enums and required top-level keys.
type WidenJson<T> = T extends string
  ? string
  : T extends number
    ? number
    : T extends boolean
      ? boolean
      : T extends null
        ? null
        : T extends readonly (infer Item)[]
          ? WidenJson<Item>[]
          : T extends object
            ? { [Key in keyof T]: WidenJson<T[Key]> }
            : T;

export const typedMockFixtures = {
  actions_ACT_0001_approve: actionsApprove,
  actions_ACT_0001_reject: actionsReject,
  actions_ACT_0002_approve: actionsFailed,
  actions_ACT_0001_retry: actionsRetry,
  alerts,
  alerts_AL_0001_read: alertRead,
  approvals,
  cases,
  cases_CASE_0001: caseDetail,
  cases_CASE_0001_close: caseClose,
  cases_CASE_0001_dismiss: caseDismiss,
  cases_CASE_0001_meeting_room: meetingRoom,
  cases_CASE_0001_meeting_room_resend: meetingRoomResend,
  cases_CASE_0001_messages: messages,
  cases_CASE_0001_messages_v2: messagesV2,
  cases_CASE_0001_options_A_approve_all: approveAll,
  cases_CASE_0001_recommendations: recommendations,
  cases_CASE_0001_recommendations_A_submit: recommendationSubmit,
  cases_CASE_0001_recommendations_v2: recommendationsV2,
  cases_CASE_0001_report: report,
  cases_CASE_0001_report_generate: reportGenerate,
  cases_CASE_0001_trace: caseTrace,
  cases_CASE_0001_v2: caseDetailV2,
  cases_CASE_0002: tariffCaseDetail,
  cases_CASE_0002_messages: tariffMessages,
  cases_CASE_0002_recommendations: tariffRecommendations,
  cases_CASE_0003: case3,
  cases_CASE_0003_v2: case3V2,
  cases_CASE_0003_recommendations: case3Recommendations,
  cases_CASE_0003_recommendations_v2: case3RecommendationsV2,
  cases_CASE_0004: case4,
  cases_CASE_0004_recommendations: case4Recommendations,
  cases_CASE_0004_meeting_room: case4MeetingRoom,
  cases_CASE_0004_decision: case4Decision,
  cases_CASE_0001_decision_custom_actions: customDecision,
  cases_CASE_0001_decision_custom_actions_catalog: customCatalog,
  cases_CASE_0004_decision_custom_actions: case4CustomDecision,
  cases_CASE_0004_decision_custom_actions_catalog: case4CustomCatalog,
  cases_CASE_0005: case5,
  cases_CASE_0005_recommendations: case5Recommendations,
  chat,
  dashboard,
  demo_scenarios: demoScenarios,
  demo_scenarios_test: demoScenariosTest,
  demo_inject_custom: demoInjectCustom,
  demo_inject_flood_karawang: demoInjectFlood,
  demo_inject_scenario: demoInjectScenario,
  demo_inject_tariff_cn: demoInjectTariff,
  demo_reset: demoReset,
  erp_log: erpLog,
  health,
  heatmap_countries: heatmap,
  me,
  me_test: meTest,
  network,
  network_all: networkAll,
  network_CASE_0002: tariffNetwork,
  network_v2: networkV2,
  risk_scan: riskScan,
  runs_RUN_20261031_0003: modeScanRun,
  runs_RUN_20261031_0007: run,
  runs_RUN_20261031_0012: reportRun,
  schedule,
  signals,
  suppliers_S01: supplier,
  suppliers_S02: supplierS02,
  suppliers_S03: supplierS03,
  suppliers_S04: supplierS04,
  suppliers_S05: supplierS05,
  suppliers_S06: supplierS06,
  suppliers_S07: supplierS07,
  suppliers_S08: supplierS08,
  suppliers_S09: supplierS09,
  suppliers_S10: supplierS10,
  suppliers_S11: supplierS11,
  suppliers_S12: supplierS12,
  suppliers_S13: supplierS13,
  suppliers_S14: supplierS14,
  suppliers_S15: supplierS15,
  system_checks: systemChecks,
  system_status: systemStatus,
  trace,
  whatif: whatIf,
  whatif_bekasi: whatIfBekasi,
  whatif_bogor: whatIfBogor,
  whatif_hamamatsu: whatIfHamamatsu,
  whatif_karawang: whatIfKarawang,
  whatif_locations: whatIfLocations,
  whatif_ningbo: whatIfNingbo,
  whatif_purwakarta: whatIfPurwakarta,
  whatif_rayong: whatIfRayong,
  whatif_shenzhen: whatIfShenzhen,
  whatif_tangerang: whatIfTangerang,
} satisfies {
  actions_ACT_0001_approve: WidenJson<ActionResult>;
  actions_ACT_0001_reject: WidenJson<ActionResult>;
  actions_ACT_0002_approve: WidenJson<ActionResult>;
  actions_ACT_0001_retry: WidenJson<ActionResult>;
  alerts: WidenJson<Alert[]>;
  alerts_AL_0001_read: null;
  approvals: WidenJson<ApprovalItem[]>;
  cases: WidenJson<CaseSummary[]>;
  cases_CASE_0001: WidenJson<CaseDetail>;
  cases_CASE_0001_close: WidenJson<CaseSummary>;
  cases_CASE_0001_dismiss: WidenJson<CaseSummary>;
  cases_CASE_0001_meeting_room: WidenJson<MeetingRoom>;
  cases_CASE_0001_meeting_room_resend: WidenJson<MeetingNotification>;
  cases_CASE_0001_messages: WidenJson<SupplierMessage[]>;
  cases_CASE_0001_messages_v2: WidenJson<SupplierMessage[]>;
  cases_CASE_0001_options_A_approve_all: WidenJson<ActionResult[]>;
  cases_CASE_0001_recommendations: WidenJson<Recommendations>;
  cases_CASE_0001_recommendations_A_submit: WidenJson<SubmittedRecommendation>;
  cases_CASE_0001_recommendations_v2: WidenJson<Recommendations>;
  cases_CASE_0001_report: WidenJson<CaseReport>;
  cases_CASE_0001_report_generate: WidenJson<RunQueuedResponse>;
  cases_CASE_0001_trace: WidenJson<AgentTrace[]>;
  cases_CASE_0001_v2: WidenJson<CaseDetail>;
  cases_CASE_0002: WidenJson<CaseDetail>;
  cases_CASE_0002_messages: WidenJson<SupplierMessage[]>;
  cases_CASE_0002_recommendations: WidenJson<Recommendations>;
  cases_CASE_0003: WidenJson<CaseDetail>;
  cases_CASE_0003_v2: WidenJson<CaseDetail>;
  cases_CASE_0003_recommendations: WidenJson<Recommendations>;
  cases_CASE_0003_recommendations_v2: WidenJson<Recommendations>;
  cases_CASE_0004: WidenJson<CaseDetail>;
  cases_CASE_0004_recommendations: WidenJson<Recommendations>;
  cases_CASE_0004_meeting_room: WidenJson<MeetingRoom>;
  cases_CASE_0004_decision: WidenJson<Decision>;
  cases_CASE_0001_decision_custom_actions: WidenJson<CustomDecisionResult>;
  cases_CASE_0001_decision_custom_actions_catalog: WidenJson<CustomActionCatalog>;
  cases_CASE_0004_decision_custom_actions: WidenJson<CustomDecisionResult>;
  cases_CASE_0004_decision_custom_actions_catalog: WidenJson<CustomActionCatalog>;
  cases_CASE_0005: WidenJson<CaseDetail>;
  cases_CASE_0005_recommendations: WidenJson<Recommendations>;
  chat: WidenJson<ChatResponse>;
  dashboard: WidenJson<Dashboard>;
  demo_scenarios: WidenJson<ScenarioList>;
  demo_scenarios_test: WidenJson<ScenarioList>;
  demo_inject_custom: WidenJson<DemoInjectResponse>;
  demo_inject_flood_karawang: WidenJson<DemoInjectResponse>;
  demo_inject_scenario: WidenJson<DemoInjectResponse>;
  demo_inject_tariff_cn: WidenJson<DemoInjectResponse>;
  demo_reset: WidenJson<DemoResetResponse>;
  erp_log: WidenJson<ErpChangeDocument[]>;
  health: WidenJson<Health>;
  heatmap_countries: WidenJson<HeatmapCountries>;
  me: WidenJson<Me>;
  me_test: WidenJson<Me>;
  network: WidenJson<Network>;
  network_all: WidenJson<Network>;
  network_CASE_0002: WidenJson<Network>;
  network_v2: WidenJson<Network>;
  risk_scan: WidenJson<RiskScanResponse>;
  runs_RUN_20261031_0003: WidenJson<AgentRun>;
  runs_RUN_20261031_0007: WidenJson<AgentRun>;
  runs_RUN_20261031_0012: WidenJson<AgentRun>;
  schedule: WidenJson<Schedule>;
  signals: WidenJson<Signal[]>;
  suppliers_S01: WidenJson<SupplierProfile>;
  suppliers_S02: WidenJson<SupplierProfile>;
  suppliers_S03: WidenJson<SupplierProfile>;
  suppliers_S04: WidenJson<SupplierProfile>;
  suppliers_S05: WidenJson<SupplierProfile>;
  suppliers_S06: WidenJson<SupplierProfile>;
  suppliers_S07: WidenJson<SupplierProfile>;
  suppliers_S08: WidenJson<SupplierProfile>;
  suppliers_S09: WidenJson<SupplierProfile>;
  suppliers_S10: WidenJson<SupplierProfile>;
  suppliers_S11: WidenJson<SupplierProfile>;
  suppliers_S12: WidenJson<SupplierProfile>;
  suppliers_S13: WidenJson<SupplierProfile>;
  suppliers_S14: WidenJson<SupplierProfile>;
  suppliers_S15: WidenJson<SupplierProfile>;
  system_checks: WidenJson<SystemStatus>;
  system_status: WidenJson<SystemStatus>;
  trace: WidenJson<AgentTrace[]>;
  whatif: WidenJson<WhatIfResult>;
  whatif_bekasi: WidenJson<WhatIfResult>;
  whatif_bogor: WidenJson<WhatIfResult>;
  whatif_hamamatsu: WidenJson<WhatIfResult>;
  whatif_karawang: WidenJson<WhatIfResult>;
  whatif_locations: WidenJson<string[]>;
  whatif_ningbo: WidenJson<WhatIfResult>;
  whatif_purwakarta: WidenJson<WhatIfResult>;
  whatif_rayong: WidenJson<WhatIfResult>;
  whatif_shenzhen: WidenJson<WhatIfResult>;
  whatif_tangerang: WidenJson<WhatIfResult>;
};
