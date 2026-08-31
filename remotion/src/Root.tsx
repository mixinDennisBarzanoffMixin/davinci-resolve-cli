import React from 'react';
import {Composition, Folder} from 'remotion';
import {BrollSegment} from './BrollSegment';
import {CaptionOverlay} from './CaptionOverlay';
import {ProductionPreview} from './ProductionPreview';
import type {ProductionProps, SegmentProps} from './types';

export const Root: React.FC = () => (
  <>
    <Folder name="Production-pipeline">
      <Composition
        id="BrollSegment"
        component={BrollSegment}
        durationInFrames={180}
        fps={60}
        width={1920}
        height={1080}
        calculateMetadata={({props}) => ({
          durationInFrames: props.durationInFrames,
          fps: props.fps,
          width: props.width,
          height: props.height,
        })}
        defaultProps={{
          durationInFrames: 180,
          fps: 60,
          width: 1920,
          height: 1080,
          accentColor: '#e51d2a',
          placement: {
            id: 'example',
            beat_id: 'feature',
            start_seconds: 0,
            duration_seconds: 3,
            on_screen_text: 'Product overview',
            visual_brief: 'Evidence-backed feature card',
            evidence_urls: [],
            must_not_show: [],
            status: 'ready-for-motion-graphic',
            treatment: {
              kind: 'motion_graphic',
              motion: {seed: 42, variant: 'auto'},
              graphic: {
                primary_text: 'Product detail',
                secondary_text: 'Evidence-backed motion graphic',
              },
            },
            asset: null,
          },
        }}
      />
      <Composition
        id="ProductionPreview"
        component={ProductionPreview}
        durationInFrames={600}
        fps={60}
        width={1920}
        height={1080}
        calculateMetadata={({props}) => ({
          fps: props.fps,
          width: props.width,
          height: props.height,
          durationInFrames: Math.max(1, Math.round(props.timelineDurationSeconds * props.fps)),
        })}
        defaultProps={{
          fps: 60,
          width: 1920,
          height: 1080,
          timelineDurationSeconds: 10,
          captions: [],
          placements: [],
          music: null,
        }}
      />
      <Composition
        id="CaptionOverlay"
        component={CaptionOverlay}
        durationInFrames={600}
        fps={60}
        width={1920}
        height={1080}
        calculateMetadata={({props}) => ({
          fps: props.fps,
          width: props.width,
          height: props.height,
          durationInFrames: Math.max(1, Math.round(props.timelineDurationSeconds * props.fps)),
        })}
        defaultProps={{
          fps: 60,
          width: 1920,
          height: 1080,
          timelineDurationSeconds: 10,
          captions: [],
          placements: [],
          music: null,
        }}
      />
    </Folder>
  </>
);
