import type {Caption} from '@remotion/captions';

export type MotionVariant =
  | 'auto'
  | 'push-in'
  | 'pull-out'
  | 'pan-left'
  | 'pan-right'
  | 'drift-up'
  | 'drift-down'
  | 'static';

export type SeededMotion = {
  seed?: number | string;
  variant?: MotionVariant;
  intensity?: number;
};

export type GraphicTreatment = {
  eyebrow?: string;
  primary_text?: string;
  secondary_text?: string;
  motif?: 'rings' | 'grid' | 'pulse';
};

type TreatmentMetadata = {
  /** Backward-compatible shorthand for `motion.seed`. */
  seed?: number | string;
  depiction_scope?: 'exact_item' | 'model_illustration' | 'conceptual' | string;
  disclosure?: string | null;
};

export type SourceCutawayTreatment = TreatmentMetadata & {
  kind: 'source_cutaway';
  motion?: SeededMotion;
  graphic?: never;
};

export type GeneratedIllustrationTreatment = TreatmentMetadata & {
  kind: 'generated_illustration' | 'generated_image';
  motion?: SeededMotion;
  graphic?: never;
};

export type EvidenceImageTreatment = TreatmentMetadata & {
  kind: 'evidence_image';
  motion?: SeededMotion;
  graphic?: never;
};

export type MotionGraphicTreatment = TreatmentMetadata & {
  kind: 'motion_graphic' | 'diagram';
  motion?: SeededMotion;
  graphic?: GraphicTreatment;
};

export type BrollTreatment =
  | SourceCutawayTreatment
  | GeneratedIllustrationTreatment
  | EvidenceImageTreatment
  | MotionGraphicTreatment;

type AssetCommon = {
  src: string;
  kind?: 'image' | 'video';
  exact_item?: boolean;
  attribution?: string;
  rights_status?: string;
  sha256?: string;
  /** Remotion-native source frame aliases. */
  trimBefore?: number;
  trimAfter?: number;
  trim_before_frames?: number;
  trim_after_frames?: number;
  trim_before_seconds?: number;
  trim_after_seconds?: number;
  source_in_seconds?: number;
  source_out_seconds?: number;
  /** B-roll source audio is muted unless explicitly enabled. */
  include_audio?: boolean;
  audio_volume?: number;
};

export type ProjectSourceAsset = AssetCommon & {
  origin: 'project_source' | 'source_cutaway';
  kind: 'video';
};

export type GeneratedAsset = AssetCommon & {
  origin: 'generated' | 'ai_generated';
  kind: 'image';
  exact_item?: false;
  generation?: {
    provider?: string;
    model?: string;
    seed?: number | string;
    prompt_sha256?: string;
  };
};

export type ApprovedEvidenceAsset = AssetCommon & {
  origin: 'approved_external' | 'listing_asset' | 'evidence_asset';
};

/**
 * Backward-compatible shape used by the first production manifests. New
 * manifests should include `origin`, but legacy `kind` / `exact_item` assets
 * remain renderable.
 */
export type LegacyBrollAsset = AssetCommon & {
  origin?: undefined;
};

export type BrollAsset =
  | ProjectSourceAsset
  | GeneratedAsset
  | ApprovedEvidenceAsset
  | LegacyBrollAsset;

export type BrollPlacement = {
  id: string;
  beat_id?: string;
  start_seconds: number;
  end_seconds?: number;
  duration_seconds?: number;
  duration_sec?: number;
  visual_type?: 'source_cutaway' | 'generated_image' | 'motion_graphic' | 'diagram' | 'exact_asset' | string;
  visual_brief?: string;
  on_screen_text?: string;
  on_screen_text_bg?: string;
  evidence_urls?: string[];
  must_not_show?: string[];
  status?: string;
  motion_seed?: number | string;
  treatment?: BrollTreatment;
  asset?: null | BrollAsset;
};

export type MusicTrack = {
  src: string;
  start_seconds?: number;
  end_seconds?: number;
  volume?: number;
  fade_in_seconds?: number;
  fade_out_seconds?: number;
  license_review_status: string;
};

export type SegmentProps = {
  placement: BrollPlacement;
  durationInFrames: number;
  fps: number;
  width: number;
  height: number;
  accentColor?: string;
};

export type ProductionProps = {
  fps: number;
  width: number;
  height: number;
  timelineDurationSeconds: number;
  captions: Caption[];
  captionReviewApproved?: boolean;
  placements: BrollPlacement[];
  music?: MusicTrack | null;
};
