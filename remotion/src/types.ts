import type {Caption} from '@remotion/captions';

export type BrollPlacement = {
  id: string;
  beat_id?: string;
  start_seconds: number;
  duration_seconds: number;
  visual_type?: string;
  visual_brief?: string;
  on_screen_text?: string;
  evidence_urls?: string[];
  must_not_show?: string[];
  asset?: null | {
    src: string;
    kind?: 'image' | 'video';
    exact_item?: boolean;
    attribution?: string;
  };
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
};
