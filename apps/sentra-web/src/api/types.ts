// Contract types for API_CONTRACT.md sections B and G. Keep names and enums
// aligned with the contract; the frontend formats values but does not recalculate them.

export type Role = 'BUYER' | 'PPIC' | 'SALES' | 'SCM_MANAGER';
export type Mode = 'LIVE' | 'DEMO';
export type Level = 'HIGH' | 'MEDIUM' | 'LOW';
export type RiskType = 'FLOOD' | 'LANDSLIDE' | 'FIRE' | 'EARTHQUAKE' | 'LOGISTICS' | 'TARIFF' | 'POLITICAL';
export type RiskScope = 'LOCATION' | 'COUNTRY';
export type SignalEngine = 'GDELT' | 'SONAR' | 'BRAVE' | 'CURRENTS' | 'DEMO';
export type SignalSourceEngine = Exclude<SignalEngine, 'DEMO'>;
export type SignalVerification = 'VERIFIED' | 'UNVERIFIED';
export type CaseStatus =
  | 'NEW'
  | 'ANALYZING'
  | 'AWAITING_SUPPLIER'
  | 'READY_FOR_DECISION'
  | 'AWAITING_APPROVAL'
  | 'EXECUTED'
  | 'CLOSED'
  | 'DISMISSED';
export type RunStatus = 'QUEUED' | 'RUNNING' | 'OK' | 'ERROR' | 'AGENT_TIMEOUT' | 'SKIPPED';
export type TraceStatus = 'RUNNING' | 'OK' | 'ERROR' | 'SKIPPED' | 'AGENT_TIMEOUT' | 'LLM_OVERRIDDEN';
export type IntegrationStatusValue = 'OK' | 'FAILED' | 'UNKNOWN';
export type VerificationStatus = 'NOT_NEEDED' | 'REQUESTED' | 'CONFIRMED_BY_SUPPLIER' | 'NO_RESPONSE';
export type Option = 'A' | 'B' | 'C' | 'D' | 'E' | 'R' | 'S';
export type RecommendationOptionCode = Option;
// Option X: the Executive Meeting Room chose its own ERP actions instead of a computed option (custom actions).
export type DecisionOption = Option | 'X';
export type RequestType = 'IMPACT' | 'BACKUP_ACTIVATION' | 'PULL_IN';
export type RequestStatus = 'REQUESTED' | 'ANSWERED' | 'NO_RESPONSE';
export type EscalationTier = 0 | 1 | 2;
export type MeetingKind = 'MEETING_ROOM' | 'EXECUTIVE_MEETING_ROOM';
export type MeetingProvider = 'ZOOM' | 'FALLBACK' | 'NONE';
export type MeetingStatus = 'SCHEDULED' | 'FAILED';
export type ParticipantRole = Role | 'CFO' | 'COO' | 'CEO' | 'LEGAL';
export type BcpBand = 'LOW_RISK' | 'MEDIUM_RISK' | 'HIGH_RISK';
export type Dataset = 'DEMO' | 'TEST';
export type EscalationReasonCode = 'M1' | 'M2' | 'M3' | 'W1' | 'W2' | 'W3';
export type ActionStatus = 'PROPOSED' | 'APPROVED' | 'EXECUTED' | 'FAILED' | 'REJECTED';

export interface SeedInfo {
  file: string;
  version: string;
  dataset: Dataset;
}

export interface FeatureFlags {
  erp_execution: boolean;
  meeting_room: boolean;
  whatif: boolean;
}

// EP-03 — GET /api/me
export interface Me {
  username: string;
  role: Role;
  mode: Mode;
  d0_date: string;
  feature_flags: FeatureFlags;
  seed: SeedInfo;
}

// EP-01 / EP-02 — authentication, included for the complete public API contract.
export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  username: string;
  role: Role;
}

// EP-04 — GET /api/schedule
export interface SchedulePOLine {
  po_id: string;
  item_no: number;
  material_id: string;
  material_name: string;
  supplier_id: string;
  supplier_name: string;
  qty: number;
  requested_date: string;
  confirmed_date: string;
  slip_days: number;
  status: 'Slipped' | 'On time';
}

export interface ScheduleProductionOrder {
  order_id: string;
  product_id: string;
  qty: number;
  start_date: string;
  sales_order_id: string;
  coverage: 'Covered' | 'Short';
  short_parts: string[];
}

export interface Schedule {
  d0_date: string;
  agent_summary: { text: string; generated_at: string; source: string } | null;
  totals: {
    po_lines: number;
    slipped: number;
    on_time: number;
    orders_covered: number;
    orders_short: number;
  };
  po_lines: SchedulePOLine[];
  production_orders: ScheduleProductionOrder[];
}

// EP-05 — GET /api/network
export type SiteHighlight = 'affected' | 'via_tier2' | 'confirmed_no_impact' | 'none';

export interface NetworkSiteNode {
  id: string;
  type: 'site';
  label: string;
  tier: '1' | '2' | 'ALT';
  kabupaten: string;
  country: string;
  bcp_score: number;
  bcp_band: BcpBand;
  highlight: SiteHighlight;
}

export interface NetworkPartNode {
  id: string;
  type: 'part';
  label: string;
  category: string;
  highlight: boolean;
}

export interface NetworkProductNode {
  id: string;
  type: 'product';
  label: string;
  highlight: boolean;
}

export type NetworkNode = NetworkSiteNode | NetworkPartNode | NetworkProductNode;

export type NetworkEdge =
  | {
      from: string;
      to: string;
      type: 'tier2_to_tier1';
      component: string;
      component_category: string;
      buffer_days: number;
      highlight: boolean;
    }
  | { from: string; to: string; type: 'supplies'; highlight: boolean }
  | { from: string; to: string; type: 'used_in'; qty_per_unit: number; highlight: boolean };

export interface Network {
  case_id: string | null;
  version: number | null;
  nodes: NetworkNode[];
  edges: NetworkEdge[];
}

// G.4 — Signal, used by EP-07 and EP-28.
interface SignalFields {
  signal_id: string;
  mode: Mode;
  engine: SignalEngine;
  simulated: boolean;
  verification: SignalVerification | null;
  risk_type: RiskType;
  country: string;
  kabupaten: string | null;
  kecamatan: string[];
  effective_date: string | null;
  tariff_pp: number | null;
  category: string | null;
  summary: string;
  downtime_days: number | null;
  source_urls: string[];
  source_label: string | null;
  evidence: SignalEvidence[];
  fetched_at: string;
  match: 'MATCHED' | 'NO_SUPPLIER_MATCH';
  case_id: string | null;
}

export type Signal = SignalFields &
  (
    | { scope: 'LOCATION'; severity: number }
    | { scope: 'COUNTRY'; severity: null }
  );

export interface SignalEvidence {
  url: string;
  published_at: string;
  date_source: string;
  engine: SignalSourceEngine;
}

// EP-06 — GET /api/suppliers/{site_id}
export interface BusinessContinuityPlan {
  score: number;
  band: BcpBand;
  categories: { governance: number; resilience: number; supply_chain: number; testing: number };
  answers: Record<'G1' | 'G2' | 'R1' | 'R2' | 'S1' | 'S2' | 'T1' | 'T2', number>;
  stated_rto_days: number | null;
  backup_site: boolean;
  emergency_plan: boolean;
  safety_stock: boolean;
  insurance: boolean;
}

export interface SupplierBackup {
  site: string | null;
  ramp_days: number | null;
  surcharge_pct: number | null;
}

export interface Tier2Supplier {
  site_id: string;
  component: string;
  component_category: string;
  buffer_days: number;
}

export interface AlternateSource {
  part_id: string;
  site_id: string;
  price: number;
  lead_time_days: number;
  max_qty: number;
  air_lead_time_days: number | null;
  air_freight_per_unit: number | null;
}

export type SupplierAnswer = 'YES' | 'PARTIAL' | 'NO';

export interface SupplierRequest {
  request_id: string;
  request_type: RequestType;
  status: RequestStatus;
  answer: SupplierAnswer | null;
}

export interface Scenario {
  key: string;
  risk_type: RiskType;
  scope: RiskScope;
  country: string;
  location: string;
  downtime_days: number | null;
  summary: string;
}

export interface ScenarioList {
  seed: SeedInfo;
  scenarios: Scenario[];
}

export interface SupplierMessage {
  message_id: string;
  case_id: string;
  site_id: string;
  request_id: string | null;
  request_type: RequestType;
  direction: 'OUT' | 'IN';
  body: string;
  structured:
    | { affected: 'YES' | 'PARTIAL' | 'NO'; delay_days: number; notes: string }
    | { answer: 'YES' | 'NO'; ramp_days: number | null; notes: string }
    | { answer: 'YES' | 'NO'; notes: string }
    | null;
  created_at: string;
  simulated: boolean;
}

export interface SupplierProfile {
  site_id: string;
  supplier_name: string;
  tier: '1' | '2' | 'ALT';
  kabupaten: string;
  kecamatan: string | null;
  country: string;
  hazard_zone_flood: boolean;
  ttr_days: number;
  otd_12m: number | null;
  backup_site: string | null;
  bcp: BusinessContinuityPlan;
  backup: SupplierBackup;
  parts_supplied: string[];
  tier2_suppliers: Tier2Supplier[];
  alternate_sources_for_parts: AlternateSource[];
  messages: SupplierMessage[];
}

// EP-08 and EP-09 — risk scan and agent run.
export interface RiskScanResponse {
  run_id: string;
  task: string;
  status: 'QUEUED';
}

export interface ScanExternalRunData {
  signals_saved?: number;
  signals_dropped?: number;
  unverified?: Array<{
    risk_type: RiskType;
    location: string;
    urls: string[];
    reason: string;
  }>;
  cases?: string[];
  sources_used?: Array<'BRAVE' | 'SONAR'>;
  evidence_stats?: {
    candidates?: number;
    screened?: number;
    in_window?: number;
    stale?: number;
    not_found?: number;
    fetch_failed?: number;
    skipped_budget?: number;
  };
  skipped_targets?: Array<{
    source: 'BRAVE' | 'SONAR';
    target: string;
    reason: 'BUDGET' | 'TIME_BUDGET' | 'NO_GOODS';
  }>;
  [key: string]: unknown;
}

export interface AgentRun {
  run_id: string;
  task: string;
  trigger: 'manual' | 'auto';
  case_id: string | null;
  status: RunStatus;
  started_at: string | null;
  finished_at: string | null;
  summary: string | null;
  error: string | null;
  /** D.3 tool result data when the run endpoint includes it. */
  data?: ScanExternalRunData | null;
}

// EP-10 — GET /api/alerts
export interface Alert {
  alert_id: string;
  case_id: string;
  level: Level;
  title: string;
  location: string;
  score: number;
  units_at_risk: number;
  revenue_at_risk: number;
  mode: Mode;
  simulated: boolean;
  created_at: string;
  read: boolean;
}

// EP-11 — GET /api/cases
export interface CaseSummary {
  case_id: string;
  title: string;
  risk_type: RiskType;
  scope: RiskScope;
  location: string;
  country: string;
  mode: Mode;
  simulated: boolean;
  status: CaseStatus;
  score: number;
  level: Level;
  units_at_risk: number;
  revenue_at_risk: number;
  updated_at: string;
}

// G.1 — Impact snapshot.
export interface PartDelay {
  part_id: string;
  days: number;
  cause_site: string | null;
  via_site: string | null;
}

export interface ImpactPOLine {
  po_id: string;
  item_no: number;
  material_id: string;
  supplier_id: string;
  qty: number;
  confirmed_date: string;
  new_eta: string;
  slip_days: number;
  value: number;
  cause_site: string;
  via_site: string | null;
}

export interface ImpactProductionOrder {
  order_id: string;
  product_id: string;
  qty: number;
  start_date: string;
  units_blocked: number;
  blocking_parts: string[];
  ready_date: string | null;
  output_value: number;
  sales_order_id: string | null;
}

export interface ImpactSalesOrder {
  so_id: string;
  customer: string;
  old_date: string;
  new_date: string | null;
  units_at_risk: number;
}

export interface ImpactTotals {
  units_blocked: number;
  output_value: number;
  po_value: number;
  rar: number;
  parts_affected: number;
  pos_affected: number;
  po_lines_affected: number;
  products_affected: string[];
  orders_blocked: number;
  max_delay_days: number;
  max_po_slip_days: number;
}

export interface TariffImpact {
  country: string;
  category: string;
  tariff_pp: number;
  effective_date: string;
  affected_value: number;
  extra_duty: number;
  not_stated: string[];
}

export interface CountryExposure {
  country: string;
  po_value: number;
  note: string;
}

export interface BaselineShort {
  order_id: string;
  units_short: number;
  short_parts: string[];
  ready_date: string | null;
}

export interface Impact {
  version: number;
  scope: RiskScope;
  part_delays: PartDelay[];
  po_lines: ImpactPOLine[];
  production_orders: ImpactProductionOrder[];
  sales_orders: ImpactSalesOrder[];
  totals: ImpactTotals;
  tariff: TariffImpact | null;
  exposure: CountryExposure | null;
  disruption: { country: string; category: string; downtime_days: number; sites: string[] } | null;
  baseline_short: BaselineShort[];
}

// G.5 — PriorityRow.
export interface PriorityComponents {
  n_rar: number;
  n_ttr: number;
  n_bcp: number;
  n_tier: number;
}

export interface PriorityRow {
  site_id: string;
  supplier_name: string;
  tier: '1' | '2' | 'ALT';
  recovery_days: number;
  rar: number;
  output_value: number;
  po_value: number;
  bcp_score: number;
  bcp_band: BcpBand;
  components: PriorityComponents;
  score: number;
}

// G.6 — CaseSite.
export interface CaseSite {
  site_id: string;
  supplier_name: string;
  tier: '1' | '2' | 'ALT';
  path: string;
  recovery_days: number;
  k_a_formula: number | null;
  p_rpt_a: number | null;
  k_a: number | null;
  k_b_formula: number | null;
  p_rpt_b: number | null;
  k_b: number | null;
  rpt_status: 'OK' | 'UNAVAILABLE';
  verification_status: VerificationStatus;
  delay_override_days: number | null;
  backup_ramp_confirmed: number | null;
  backup_declined: boolean;
  backup_site: string | null;
  requests: SupplierRequest[];
  excluded: boolean;
}

export interface CaseSignal {
  signal_id: string;
  summary: string;
  severity: number | null;
  source_urls: string[];
  source_label: string | null;
}

export interface CaseKpi {
  units_at_risk: number;
  delayed_output_value: number;
  affected_po_value: number;
  revenue_at_risk: number;
  priority_score: number;
  level: Level;
  status: CaseStatus;
}

export interface CaseVersion {
  version: number;
  trigger: 'initial' | 'supplier_reply' | 'erp_execution';
  created_at: string;
}

export interface CaseDetail {
  case_id: string;
  title: string;
  risk_type: RiskType;
  scope: RiskScope;
  location: string;
  country: string;
  mode: Mode;
  simulated: boolean;
  status: CaseStatus;
  d0_date: string;
  created_at: string;
  updated_at: string;
  version: number;
  versions: CaseVersion[];
  signal: CaseSignal;
  kpi: CaseKpi;
  impact: Impact;
  sites: CaseSite[];
  priority: PriorityRow[];
  preliminary: boolean;
  escalation: Escalation;
}

// EP-13 — agent trace.
export interface AgentTrace {
  trace_id: string;
  run_id: string | null;
  task: string | null;
  case_id: string | null;
  step: string;
  tool: string | null;
  input_summary: string | null;
  output_summary: string | null;
  decision: string | null;
  duration_ms: number | null;
  status: TraceStatus;
  created_at: string;
}

// EP-14 — supplier messages.
export type SupplierMessages = SupplierMessage[];

// G.2 and G.3 — Recommendation metrics and ERP action payloads.
export interface Metrics {
  units_blocked: number;
  output_value: number;
  po_value: number;
  rar: number;
  so_delayed: number;
  premium: number;
  extra_duty: number | null;
}

export type ActionType =
  | 'CHANGE_PO_DATE'
  | 'CREATE_PR_PO'
  | 'REDUCE_OR_CANCEL_PO'
  | 'RESCHEDULE_PRODUCTION_ORDER'
  | 'RESCHEDULE_SALES_ORDER'
  | 'EXPEDITE_SHIPMENT';

export type ActionPayload =
  | { po_id: string; item_no: number; old_date: string; new_date: string }
  | { material_id: string; supplier_id: string; qty: number; price: number; delivery_date: string;
      shipping_condition: 'ROAD' | 'SEA' | 'AIR'; incoterms: 'FCA' | 'FOB'; freight_per_unit: number }
  | { po_id: string; item_no: number; old_qty: number; new_qty: number }
  | { order_id: string; old_date: string; new_date: string }
  | { so_id: string; old_date: string; new_date: string }
  | { po_id: string; item_no: number; old_shipping_condition: 'ROAD' | 'SEA'; new_shipping_condition: 'AIR';
      old_incoterms: 'FCA' | 'FOB'; new_incoterms: 'FCA'; freight_cost: number };

export interface RecommendationAction {
  seq: number;
  type: ActionType;
  object: string;
  payload: ActionPayload;
}

export interface Action extends RecommendationAction {
  action_id: string;
  status: ActionStatus;
  idempotency_key: string;
}

export interface RecommendationOption {
  option: RecommendationOptionCode;
  title: string;
  available: boolean;
  unavailable_reason: string | null;
  label: string | null;
  pending_confirmation: boolean;
  confirmation: { request_type: RequestType; sites: { site_id: string; status: RequestStatus | null; answer: SupplierAnswer | null }[] } | null;
  saving: number | null;
  before: Metrics;
  after: Metrics | null;
  actions: RecommendationAction[];
  rationale: string;
  rationale_source: 'llm' | 'template';
}

export interface Recommendations {
  case_id: string;
  version: number;
  preliminary: boolean;
  recommended_option: RecommendationOptionCode | null;
  rule_reason: string | null;
  execution_enabled: boolean;
  confirmations_needed: { site_id: string; request_type: RequestType }[];
  escalation_tier: EscalationTier;
  executive_decision_required: boolean;
  decision: Decision | null;
  options: RecommendationOption[];
  non_erp_steps: string[];
}

// EP-16 — country heatmap.
export interface HeatmapOpenCase {
  case_id: string;
  title: string;
  level: Level;
  score: number;
}

export interface HeatmapSite {
  site_id: string;
  supplier_name: string;
  tier: '1' | '2' | 'ALT';
  kabupaten: string;
}

export interface HeatmapCountry {
  country: string;
  name: string;
  sites_t1: number;
  sites_t2: number;
  open_po_lines: number;
  po_exposure: number;
  share: number;
  exposure_tier: 1 | 2 | 3 | 4;
  highest_level: Level | null;
  open_cases: HeatmapOpenCase[];
  sites: HeatmapSite[];
}

export interface HeatmapCountries {
  total_po_exposure: number;
  simulated_cases_present: boolean;
  countries: HeatmapCountry[];
}

// EP-17 — report, derived from a case snapshot.
export interface ReportCharts {
  rar_by_site: { site_id: string; rar: number }[];
  delay_by_part: { part_id: string; days: number }[];
  units_by_order: { order_id: string; units: number }[];
  rar_by_version: { version: number; rar: number }[];
}

export interface CaseReport {
  case_id: string;
  version: number;
  generated_at: string;
  simulated: boolean;
  file_title: string;
  narrative_md: string;
  narrative_source: 'llm' | 'template';
  sections: string[];
  data: { kpi: CaseKpi; recommendation: Recommendations; actions: Action[] };
  charts: ReportCharts;
}

export interface RunQueuedResponse {
  run_id: string;
  status: 'QUEUED';
}

// EP-18 — chat.
export interface ChatRequest {
  case_id: string | null;
  message: string;
  chat_session_id: string | null;
}

export interface CaseDispositionRequest {
  comment?: string;
}

export interface DismissCaseRequest {
  comment: string;
}

export interface RejectActionRequest {
  comment: string;
}

export interface ChatCitation {
  type: 'record' | 'url';
  id?: string;
  url?: string;
}

export interface ChatResponse {
  answer: string;
  citations: ChatCitation[];
  chat_session_id: string;
  validated: boolean;
}

// EP-20 — Meeting Room (F-07).
export interface MeetingNotification {
  status: 'SENT' | 'FAILED';
  kind: MeetingKind;
  meeting_id: string;
  recipients: string[];
  sent_at: string | null;
  error: string | null;
}

export interface EscalationReason {
  code: EscalationReasonCode;
  text: string;
}

export interface Meeting {
  meeting_id: string;
  case_id: string;
  tier: EscalationTier;
  kind: MeetingKind;
  provider: MeetingProvider;
  status: MeetingStatus;
  join_url: string | null;
  start_time: string | null;
  duration_min: number;
  topic: string;
  agenda: string;
  agenda_source: 'llm' | 'template';
  participants: ParticipantRole[];
  required_approvers: ParticipantRole[];
  reasons: EscalationReason[];
  case_version: number;
  error: string | null;
  created_at: string;
}

export interface Decision {
  decision_id: string;
  case_id: string;
  meeting_id: string;
  case_version: number;
  option: DecisionOption | null;
  budget_approved: boolean;
  approved_by: ParticipantRole[];
  notes: string | null;
  decided_by: string;
  decided_at: string;
}

export interface DecisionRequest {
  meeting_id: string;
  option: Option | null;
  budget_approved: boolean;
  approved_by: ParticipantRole[];
  notes?: string;
}

// Custom ERP actions of an Executive Meeting Room decision (Option X): the object and the new value only. The server reads the
// old values, price, freight and delivery date from the ERP and checks every action with R-17 before anything is written.
export interface CustomActionRequest {
  type: ActionType;
  po_id?: string;
  item_no?: number;
  new_date?: string;
  new_qty?: number;
  order_id?: string;
  so_id?: string;
  material_id?: string;
  supplier_id?: string;
  qty?: number;
  by_air?: boolean;
}

export interface CustomDecisionRequest {
  meeting_id: string;
  budget_approved: boolean;
  approved_by: ParticipantRole[];
  notes?: string;
  actions: CustomActionRequest[];
}

export interface CustomDecisionResult {
  // Tier 2: the decision note (option X). Tier 1 (Meeting Room) has no decision note: null.
  decision: Decision | null;
  case_status: CaseStatus;
  premium: number;
  actions: ActionResult[];
}

export interface CustomCatalogPoLine {
  po_id: string;
  item_no: number;
  material_id: string;
  material_name: string;
  supplier_id: string;
  supplier_name: string;
  qty: number;
  confirmed_date: string;
  shipping_condition: string;
  incoterms: string;
  expedite_cost_per_unit: number | null;
  affected: boolean; // in the impact of this case
}

export interface CustomCatalogProductionOrder {
  order_id: string;
  product_id: string;
  product_name: string;
  qty: number;
  start_date: string;
  status: string;
  affected: boolean;
}

export interface CustomCatalogSalesOrder {
  so_id: string;
  customer: string;
  product_id: string;
  product_name: string;
  qty: number;
  confirmed_delivery_date: string;
  affected: boolean;
}

export interface CustomCatalogSource {
  material_id: string;
  material_name: string;
  supplier_id: string;
  supplier_name: string;
  country: string | null;
  price: number;
  max_qty: number;
  lead_time_days: number;
  delivery_date: string;
  air_lead_time_days: number | null;
  air_freight_per_unit: number | null;
  air_delivery_date: string | null;
  affected: boolean; // the material is delayed or blocking in this case
}

export interface CustomActionCatalog {
  case_id: string;
  option: 'X';
  today: string;
  max_actions: number;
  po_lines: CustomCatalogPoLine[];
  production_orders: CustomCatalogProductionOrder[];
  sales_orders: CustomCatalogSalesOrder[];
  alternate_sources: CustomCatalogSource[];
}

export interface EscalationLimits {
  scm_approval_limit: number;
  tariff_escalation_duty: number;
  close_call_band_pp: number;
  premium_max_pct: number;
}

export interface Escalation {
  case_id: string;
  version: number;
  tier: EscalationTier;
  kind: MeetingKind | null;
  reasons: EscalationReason[];
  participants: ParticipantRole[];
  required_approvers: ParticipantRole[];
  recommended_option: Option | null;
  premium: number | null;
  extra_duty: number | null;
  limits: EscalationLimits;
  meeting_tier: EscalationTier;
  needs_meeting: boolean;
  decision_required: boolean;
  meeting: Meeting | null;
  decision: Decision | null;
}

export interface MeetingRoom {
  case_id: string;
  title: string;
  simulated: boolean;
  score: number;
  level: Level;
  status: CaseStatus;
  updated_at: string;
  escalation: Escalation;
  meetings: Meeting[];
  notification: MeetingNotification | null;
  sites: (PriorityRow & { verification_status: VerificationStatus })[];
  latest_recommendation: { recommended_option: RecommendationOptionCode | null; preliminary: boolean; rule_reason: string | null };
}

// EP-21 / EP-22 — approval workflow (F-12).
export interface SubmittedRecommendation {
  case_id: string;
  option: RecommendationOptionCode;
  status: CaseStatus;
  actions: Action[];
}

export interface ApprovalItem {
  action_id: string;
  case_id: string;
  case_title: string;
  option: RecommendationOptionCode;
  seq: number;
  type: ActionType;
  object: string;
  payload: ActionPayload;
  status: ActionStatus;
  proposed_by: string;
  created_at: string;
  // EP-22: backend juga mengirim jawaban ERP (FAILED: alasan gagal). Opsional di runtime (item buatan EP-21, data lama).
  erp_result?: ErpResult | null;
  error?: string | null;
}

// EP-22 v1.12 — BAPIRET2-style message of the ERP (E.2.2).
export interface ErpMessage {
  type: 'S' | 'W' | 'E' | 'I';
  id: string;
  number: string;
  message: string;
}

// EP-22 v1.12 — `erp_result`: `document` null and the refusal messages when FAILED; no messages before v1.12.
export interface ErpResult {
  document: string | null;
  messages?: ErpMessage[];
}

export interface ActionResult extends Action {
  erp_result?: ErpResult | null;
  error?: string | null;
}

// EP-23 — GET /api/erp-log (mock-s4 E-17 change_document). The CDHDR/CDPOS columns
// (v1.12) are optional because rows written before v1.12 do not have them.
export interface ErpChangeDocument {
  id: string | number;
  object_type: string;
  object_id: string;
  field: string;
  old_value: string | null;
  new_value: string | null;
  changed_by: string;
  changed_at: string;
  source: string;
  change_number?: string;
  object_class?: string;
  object_key?: string;
  table_name?: string;
  table_key?: string;
  field_name?: string;
  field_label?: string;
  change_ind?: 'U' | 'I' | 'D';
  tcode?: string;
  username?: string;
}

// EP-24 — demo control.
export interface DemoResetResponse {
  status: 'OK';
  d0_date: string;
}

interface DemoInjectSignalFields {
  risk_type: RiskType;
  country: string;
  kabupaten: string | null;
  kecamatan: string[];
  effective_date: string | null;
  tariff_pp: number | null;
  category: string | null;
  summary: string;
  downtime_days: number | null;
  source_urls: string[];
}

export type DemoInjectSignal = DemoInjectSignalFields &
  (
    | { scope: 'LOCATION'; severity: number }
    | { scope: 'COUNTRY'; severity: null }
  );

export interface DemoInjectRequest {
  scenario: string | 'custom';
  signal?: DemoInjectSignal;
}

export interface DemoInjectResponse {
  signal_id: string;
  case_id: string;
  run_id: string;
  status: 'QUEUED';
}

// EP-25 — What-if (F-10).
export interface WhatIfKpi {
  parts_affected: number;
  pos_affected: number;
  po_lines_affected: number;
  products_affected: string[];
  units_blocked: number;
  orders_blocked: number;
  max_delay_days: number;
  max_po_slip_days: number;
  output_value: number;
  po_value: number;
  rar: number;
}

export interface WhatIfResult {
  location: string;
  banner: string;
  sites_t1: string[];
  sites_t2: string[];
  kpi: WhatIfKpi;
  impact: Impact;
  charts: {
    rar_by_site: { site_id: string; rar: number }[];
    delay_by_part: { part_id: string; days: number }[];
    units_by_product: { product_id: string; units: number }[];
  };
}

export interface WhatIfRequest {
  location: string;
}

// EP-26 — system status.
export interface IntegrationStatus {
  name: IntegrationName;
  status: IntegrationStatusValue;
  last_call_at: string | null;
  duration_ms: number | null;
  message: string | null;
}

export type IntegrationName =
  | 'claude-4.6-sonnet'
  | 'sap-rpt-1.6'
  | 'sonar'
  | 'brave'
  | 'agentcore'
  | 'zoom'
  | 'gmail';

export interface SchedulerStatus {
  auto_scan: 'ON' | 'OFF_DEMO';
  interval_min: number;
  last_run: unknown;
  next_run_at: string | null;
  daily_check: { last_run_at: string | null; status: string; summary: string } | null;
}

export interface SystemStatus {
  mode: Mode;
  d0_date: string;
  seed: SeedInfo;
  integrations: IntegrationStatus[];
  feature_flags: FeatureFlags;
  scheduler: SchedulerStatus | null;
}

// EP-29 — application mode.
export interface SystemModeStatus {
  mode: Mode;
  live_since: string | null;
  changed_by: string | null;
  scan_interval_min: number;
  next_scan_at: string | null;
  scan_run_id?: string | null;
}

export interface SetSystemModeRequest {
  mode: Mode;
}

// EP-27 — health.
export interface Health {
  status: 'ok';
  version: string;
}

// EP-28 — dashboard.
export interface Dashboard {
  kpi: {
    open_cases: number;
    high_alerts: number;
    revenue_at_risk_open_cases: number;
  };
  schedule: { slipped: number; on_time: number; orders_short: number };
  latest_signals: Signal[];
  unread_alerts: number;
}

// Error envelope from API_CONTRACT.md A.3.
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
}

// Request/response index for the public endpoint groups in section B.
export interface EndpointResponses {
  EP01: LoginResponse;
  EP02: undefined;
  EP03: Me;
  EP04: Schedule;
  EP05: Network;
  EP06: SupplierProfile;
  EP07: Signal[];
  EP08: RiskScanResponse;
  EP09: AgentRun;
  EP10: Alert[];
  EP10Read: undefined;
  EP11: CaseSummary[];
  EP12: CaseDetail;
  EP13: AgentTrace[];
  EP14: SupplierMessages;
  EP15: Recommendations;
  EP16: HeatmapCountries;
  EP17: CaseReport;
  EP17Generate: RunQueuedResponse;
  EP18: ChatResponse;
  EP19Close: CaseSummary;
  EP19Dismiss: CaseSummary;
  EP20: MeetingRoom;
  EP20Resend: MeetingNotification;
  EP30: Decision;
  EP31: ScenarioList;
  EP21: SubmittedRecommendation;
  EP22Approvals: ApprovalItem[];
  EP22Action: ActionResult;
  EP22ApproveAll: ActionResult[];
  EP23: ErpChangeDocument[];
  EP24Reset: DemoResetResponse;
  EP24Inject: DemoInjectResponse;
  EP25Locations: string[];
  EP25WhatIf: WhatIfResult;
  EP26: SystemStatus;
  EP26Checks: SystemStatus;
  EP29: SystemModeStatus;
  EP29Update: SystemModeStatus;
  EP27: Health;
  EP28: Dashboard;
}

// Request body types for the operations in API_CONTRACT.md section B.
export interface EndpointRequests {
  EP01: LoginRequest;
  EP02: undefined;
  EP03: undefined;
  EP04: undefined;
  EP05: undefined;
  EP06: undefined;
  EP07: undefined;
  EP08: undefined;
  EP09: undefined;
  EP10Read: undefined;
  EP11: undefined;
  EP12: undefined;
  EP13: undefined;
  EP14: undefined;
  EP15: undefined;
  EP16: undefined;
  EP17Generate: undefined;
  EP18: ChatRequest;
  EP19Close: CaseDispositionRequest;
  EP19Dismiss: DismissCaseRequest;
  EP20Resend: undefined;
  EP30: DecisionRequest;
  EP31: undefined;
  EP21Submit: undefined;
  EP22Approve: undefined;
  EP22Reject: RejectActionRequest;
  EP22Retry: undefined;
  EP22ApproveAll: undefined;
  EP23: undefined;
  EP24Reset: undefined;
  EP24Inject: DemoInjectRequest;
  EP25Locations: undefined;
  EP25WhatIf: WhatIfRequest;
  EP26Checks: undefined;
  EP29: undefined;
  EP29Update: SetSystemModeRequest;
  EP27: undefined;
  EP28: undefined;
}
