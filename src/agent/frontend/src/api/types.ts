import type {ObservationTrace} from '../executionObservations';
export type ExecutionRun={id:string;kind:string;engine:string;path:string[];stage:string;revision?:number;time?:string;changes:string[]};
export type ChartPoint = {
  date: string;
  value: number;
};

export type ChartSeries = {
  frequency?: string;
  id: string;
  label: string;
  unit: string;
  color: string;
  points: ChartPoint[];
};

export type ChartType = "line" | "bar" | "stacked_bar" | "area" | "scatter" | "bubble" | "pie" | "donut" | "histogram" | "box" | "heatmap" | "treemap" | "waterfall";
export type ChartLayout = "combined" | "separate" | "horizontal" | "grid";

export type SelectedTable = {
  tableId: string;
  name: string;
  source: string;
  item: string;
  unit: string;
};

export type LineageStep = {
  id: string;
  title: string;
  description: string;
  status: "complete" | "active";
};

export type ClarificationOption = {
  label: string;
  value: string;
};

export type Clarification = {
  id: string;
  question: string;
  ui: string;
  options: ClarificationOption[];
};

export type QueryState = {
  original_query?: string;
  confirmed?: Record<string, string>;
  confirmed_terms?: string[];
  asked_clarifications?: string[];
  candidate_tables?: Array<{ table_id: string; table_name: string; score: number }>;
  status?: string;
  [key: string]: unknown;
};

export type QueryResponse = {
  observability?:ObservationTrace[];
  availability?:{status:string;checkId?:string;calculationGuide?:string;checkedAt?:string;checkedTables:number;requiredTables:number;origin?:string;sha256?:string;calculationExecuted?:boolean;
    questions:string[];sources:Array<{name:string;status:string;reason:string;unit:string;quotation:string;
      periods:Array<{label:string;start:string;end:string;frequency:string}>;
      candidateTables:Array<{tableId:string;name:string;frequencies:string[]}>;
      observationChecks?:Array<{status:string;observations?:number;unit?:string;reason?:string}>;
      guidance?:{title:string;description:string;links:Array<{label:string;url:string;note:string}>}|null}>};
  supplement?:{workspaceId:string;revision:number;calculationGuide?:string;
    comparison?:{periods:Array<{label:string;start:string;end:string;frequency:string}>;series:Array<{alias:string;label:string;unit:string}>;configured:boolean;settings?:{periods:Array<{label:string;start:string;end:string;basis:string}>;series:Array<{alias:string;label:string;direction:string}>;average_aliases:string[];complete_observations_confirmed:boolean}}|null;
    acceptedFormats?:string[];maxFileBytes?:number;maxRows?:number;question?:string|null;
    missing:Array<{alias:string;required_data:string;reason:string;unit:string;frequency:string;lookback:number;periods?:Array<{label:string;start:string;end:string;frequency:string}>;
      guidance?:{kind:string;title:string;description:string;searchTerms:string;verifiedAt:string;links:Array<{label:string;url:string;note:string}>;checklist:string[]}}>;
    available:Array<{required_data:string;origin:string;tableId:string;observations:number}>;
    uploaded:Array<{alias:string;filename:string;sha256:string;origin:string}>};
  analysisKind?: 'derived';
  missingData?: Array<{required_data:string;reason:string}>;
  derivedEvidence?: {
    categoryAverages?:Array<{label:string;end:string;nodes:string[];formula:string;value:number;unit:string}>;
    calculationId:string;sha256:string;catalogVersion:string;
    plan:{indicator_name:string;economic_definition:string;start_period:string;end_period:string;
      nodes:Array<{id:string;operator:string;inputs:string[];lag?:number|null;window?:number|null;base_period?:string|null;exponent?:number|null;direction?:string|null;periods?:Array<{label:string;start:string;end:string;basis:string}>|null}>};
    bounds:{fetch_start:string;fetch_end:string};
    sourceSelections:Array<{alias:string;selection:string;tableId?:string;itemId?:string;frequency?:string;classifications?:Record<string,string>;origin?:string;sha256?:string;filename?:string;provenance?:string[]}>;
    issues:Array<{node:string;period:string;reason:string}>;missingPolicy:string;
  };
  requiresQueryRefinement?: boolean;
  refinementQuestion?: string;
  recovery?: {issue:string;requirements?:string[];question:string;choices:Array<{id:string;label:string}>;preserveState:boolean};
  executionRuns?:ExecutionRun[];
  periodSelection?: {source:"text"|"ui"|"latest_observation";basis?:string;requested?:{start:string;end:string};applied?:{start:string;end:string};start?:string;end?:string};
  requestValidation?: {valid:boolean;request:Record<string,unknown>;errors:string[];table_ids:string[]};
  status?: "need_details" | "need_clarification" | "need_period" | "need_output_config" | "resolved" | "no_match" | "data_unavailable" | "request_failed";
  query: string;
  interpretedQuery: string;
  summary: string;
  period: { start: string; end: string };
  availablePeriod?: { min: string; max: string };
  frequency: string;
  chart: ChartSeries[];
  chartMode?: ChartLayout | null;
  chartType?: ChartType;
  outputSpec?: {
    cachedSnapshot?: boolean;
    chatReadOnly?: boolean;
    status: "ready" | "empty";
    agent: string;
    summary: string;
    table: {
      columns: string[];
      rows: Array<{ series: string; period: string; value: number; unit: string }>;
    };
    explanation: { text: string; method: string; source: string;
      factIds?:string[]; facts?:Array<{id:string;text:string;formula?:string;result?:string;unit?:string;observations:Array<{date:string;value:number}>}>;
      scope?:{basis:string;transform:string;labels:string[];periodStart?:string;periodEnd?:string};
    };
    evidence: Array<{
      tableId: string;
      tableName: string;
      organizationId: string;
      itemId: string;
      frequency: string;
      requestedPeriod: { start: string; end: string };
      rowCount: number | null;
      source: string;
    }>;
    plotlyFigure?: { data: Array<Record<string, unknown>>; layout: Record<string, unknown> };
    chartState?: Record<string, unknown>;
    echartsOption?: Record<string, unknown>;
    echartsUnavailable?: string;
    reportDocument?: Record<string, unknown>;
    tableDocument?: import('../reportTable').TableDocument;
    tableSeries?: ChartSeries[];
    editAgent?: { model: string; supportsImages: boolean; markCount: number; path: string[] };
    editCapabilities?: { model: string; supportsImages: boolean };
    editVersion?: number;
    editChanges?: string[];
    canUndo?: boolean;
    canRedo?: boolean;
    visualization: {
      chartType: ChartType;
      layout: ChartLayout;
      editable: boolean;
      editOptions: {
        title?: string | null;
        showLegend?: boolean;
        xAxisLabel?: string | null;
        yAxisLabel?: string | null;
        supportedChartTypes?: ChartType[];
        supportedLayouts?: ChartLayout[];
      };
    };
  };
  seriesCount?: number;
  outputSessionId?: string;
  outputSessionIds?: string[];
  editSessionId?: string;
  outputOptions?: {
    seriesCount: number;
    pointCount: number;
    recommendedChartType: ChartType;
    supportedChartTypes: ChartType[];
    unavailableChartTypes?: Partial<Record<ChartType,string>>;
    combinedLayoutReason?: string;
    supportedLayouts: ChartLayout[];
    editableFields: string[];
  };
  tables: SelectedTable[];
  insights: string[];
  lineage: LineageStep[];
  warnings: string[];
  clarification?: Clarification;
  clarifications?: Clarification[];
  state?: QueryState;
  debug?: {
    orchestration?: {engine:string;stage:string;path:string[]};
    selectedTable?: unknown;
    apiPlan?: Record<string, unknown>;
    executionStatus?: string;
    rowsPreview?: Array<Record<string, unknown>>;
    timingMs?: { resolve?: number; execute?: number; total?: number };
  };
};

export type OutputRenderRequest = {
  quick_output?: boolean;
  session_ids: string[];
  chart_type: "auto" | ChartType;
  chart_mode: "combined" | "separate";
  title?: string;
  show_legend: boolean;
  x_axis_label?: string;
  y_axis_label?: string;
  natural_language?: string;
};

export type ShapeAnchor = {label:string;start:string;end:string;start_offset:number;end_offset:number;y0:number;y1:number};
export type SketchRegion = {x0:number;x1:number;y0:number;y1:number;data_anchor?:ShapeAnchor};
export type SketchMark = {region?:SketchRegion;
  id: string;
  tool: "pen" | "arrow" | "rectangle" | "ellipse" | "text";
  points: Array<{ x: number; y: number }>;
  text: string;
  target: string;
  selection?: EditSelection;
};
export type EditSelection = { label:string; scope:"series"|"segment"|"point"; start?:string; end?:string };
export type VisualEditContext = {
  marks: SketchMark[];
  selected_target: string;
  selection?: EditSelection;
  graph_image?: string;
  marked_image?: string;
};
export type ChartSticker = {id:string;asset_id:string;x:number;y:number;size:number;angle:number;opacity:number};
export type ChartReference = {name:string;mime:string;data_url:string};
export type StickerAsset = {id:string;name:string;category:string;file:string;width:number;height:number};
export type ChartEditInput = { edit_target?:'graph'|'table'; reference_files?:ChartReference[]; undo_property?:'title'|'subtitle'|'legend_position'|'series_color'; property_label?:string; append_session_ids?:string[]; replace_session_ids?:string[]; restore_initial?:boolean; instruction: string; stickers?: ChartSticker[]; report?:Record<string,unknown>; clear_report?:boolean; document?:Record<string,unknown>; commands?:Array<{operation:string;kind:"STYLE_EDIT"|"DATA_EDIT";value?:unknown;params?:Record<string,unknown>;label?:string;start?:string;end?:string;x_axis_label?:string;y_axis_label?:string}>; conversation?: Array<{role:"user"|"assistant";text:string}>; visual?: VisualEditContext; action?:"edit"|"undo"|"redo"; revision?:number };
export type OutputEditRequest = ChartEditInput & { edit_session_id: string };

export type QueryRequest = {
  query: string;
  state?: QueryState;
  clarification?: {
    clarification_id: string;
    value: string;
  };
  selections?: Array<{ clarification_id: string; values: string[] }>;
  dimension_values?: Record<string, string>;
  execute?: boolean;
  period_start?: string;
  period_end?: string;
  chart_mode?: "combined" | "separate";
  chart_type?: "auto" | ChartType;
  chart_options?: {
    title?: string;
    show_legend?: boolean;
    x_axis_label?: string;
    y_axis_label?: string;
  };
};

export type CatalogTable = {
  tableId: string; name: string; organization: string; frequency: string; frequencyLabel: string; unitScale: string;
  periodStart: string; periodEnd: string; items: string[]; units: string[];
  dimensions: Array<{ name: string; count: number; values: string[]; apiParam: string; valueOptions: Array<{id: string; name: string}>; defaultValueId: string }>;
};
export type CatalogMiddle = { name: string; count: number; children: CatalogTable[] };
export type CatalogMajor = { name: string; count: number; children: CatalogMiddle[] };
export type CatalogResponse = { source: string; total: number; categories: CatalogMajor[] };
