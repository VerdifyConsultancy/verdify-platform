import buildDocument from "../../.generated/build.json";
import recordDocument from "../../.generated/content-records.json";

export type GrafanaOccurrence = {
  occurrenceId: string;
  route: string;
  ordinal: number;
  semanticRole: string;
  uid: string;
  panelId: string;
  query: Record<string, string[]>;
  variables: Record<string, string[]>;
  timeRange: { from: string; to: string };
  liveUrl: string;
  renderCadenceSeconds: number;
};

export type CurrentMediaOccurrence = {
  occurrenceId: string;
  route: string;
  ordinal: number;
  classification: "current-still";
  semanticRole: string;
  sourceProvenanceSha256: string;
  stableTarget: string;
  captureCadenceSeconds: number;
};

export type ContentRecord = {
  route: string;
  canonicalPath: string;
  canonicalUrl: string;
  physicalPath: string;
  kind: "root" | "page" | "folder" | "alias" | "tag";
  source: string;
  title: string;
  description: string;
  html: string;
  aliases: string[];
  tags: string[];
  cssclasses: string[];
  noindex: boolean;
  target: string;
  grafana: GrafanaOccurrence[];
  currentMedia: CurrentMediaOccurrence[];
  date: string;
  socialImage: string;
};

export type BuildDocument = {
  contract: string;
  schemaVersion: number;
  siteOrigin: string;
  stageGlobalNoindex: boolean;
  snapshotId: string;
  snapshotManifestDigest: string;
  sanitization: {
    fixtureOnly: boolean;
    sourceManifestSha256: string | null;
    sanitizedManifestSha256: string | null;
    policyVersion: string;
    guardReportSha256: string | null;
    transformations: {
      changedFiles: number;
      textRedactionFiles: number;
      invalidValueRepairFiles: number;
      pngReencodeFiles: number;
      hlsFilesPreserved: number;
    } | null;
  };
  localEvidenceStatus: string;
  approvalEligible: boolean;
  mandatoryApprovalBoundary: string;
  sourceCount: number;
  snapshotMarkdownCount: number;
  excludedDrafts: string[];
  aliasCount: number;
  rollingPlanCompatibility: {
    contract: string;
    route: string;
    target: string | null;
    selectedSource: string | null;
    suppressedDeclarationCount: number;
    suppressedSources: string[];
  };
  tagRouteCount: number;
  folderRouteCount: number;
  grafanaOccurrenceCount: number;
  cameraOccurrenceCount: number;
  cameraLocalFallbackCount: number;
  unavailableReferenceCount: number;
  currentMediaOccurrenceCount: number;
  selectedOccurrenceManifestSha256: string | null;
  occurrenceManifestDigest: string;
  materializedOccurrenceBlobCount: number;
  routeDigest: string;
  snapshotAssetCount: number;
  copiedSnapshotAssetCount: number;
  generatedResponsiveImageCount: number;
  policyReplacedAssets: string[];
  preservedMediaCount: number;
  siteShell: {
    contractVersion: string;
    wwwCommit: string;
    archiveDigest: string;
    releaseDigest: string;
    manifestDigest: string;
  };
};

export const records = recordDocument as ContentRecord[];
export const build = buildDocument as BuildDocument;

export function recordForRoute(route: string) {
  return records.find((record) => record.route === route);
}

export const latestPlan = records
  .filter((record) => /^\/plans\/\d{4}-\d{2}-\d{2}$/.test(record.route))
  .sort((left, right) => right.route.localeCompare(left.route))[0];
