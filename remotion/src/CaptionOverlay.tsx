import React from 'react';
import {AbsoluteFill} from 'remotion';
import {AnimatedCaptions} from './AnimatedCaptions';
import type {ProductionProps} from './types';

export const CaptionOverlay: React.FC<ProductionProps> = ({captions}) => (
  <AbsoluteFill style={{backgroundColor: 'transparent'}}>
    <AnimatedCaptions captions={captions} />
  </AbsoluteFill>
);
