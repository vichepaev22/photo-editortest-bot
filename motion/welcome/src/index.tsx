import {Composition, registerRoot} from 'remotion';
import {WelcomeFilm} from './Film';
const Root=()=> <Composition id="ObrazWelcome" component={WelcomeFilm} width={1080} height={1920} fps={30} durationInFrames={660}/>;
registerRoot(Root);
