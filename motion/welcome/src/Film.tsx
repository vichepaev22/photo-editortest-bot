import React from 'react';
import {useCurrentFrame,useVideoConfig} from 'remotion';
import {WelcomeScene} from './Scene';
export const WelcomeFilm:React.FC=()=>{
  const frame=useCurrentFrame(),{fps}=useVideoConfig();
  return <WelcomeScene t={frame/fps}/>;
};
