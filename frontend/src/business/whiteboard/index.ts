import {type WhiteboardPorts} from "./ports";
import {createWhiteboardDocument} from "./document";
import {createWhiteboardWorkflow} from "./workflow";
import {createWhiteboardActions} from "./actions";
import {createWhiteboardSurfaces} from "./surfaces";
import {createWhiteboardFiles} from "./files";
import {createWhiteboardEquipment} from "./equipment";
import {createWhiteboardCapacity} from "./capacity";
import {createWhiteboardAdmission} from "./admission";
import {createWhiteboardView} from "./view";
import {createWhiteboardProjection} from "./projection";
import {createWhiteboardSvg} from "./svg";
import {createWhiteboardPointer} from "./pointer";
import {createWhiteboardInspector} from "./inspector";
export function createWhiteboardBusiness(ports:WhiteboardPorts){
 return Object.assign({},
 createWhiteboardDocument(ports),
 createWhiteboardWorkflow(ports),
 createWhiteboardActions(ports),
 createWhiteboardSurfaces(ports),
 createWhiteboardFiles(ports),
 createWhiteboardEquipment(ports),
 createWhiteboardCapacity(ports),
 createWhiteboardAdmission(ports),
 createWhiteboardView(ports),
 createWhiteboardProjection(ports),
 createWhiteboardSvg(ports),
 createWhiteboardPointer(ports),
 createWhiteboardInspector(ports)
 );
}
