/** Single mechanics-editor owner assembled from responsibility modules. */
import type {MechanicsEditor, MechanicsEditorPorts} from "./contracts";
import {installMechanicsVocabulary} from "./vocabulary";
import {installMechanicsSchemaForm} from "./schema-form";
import {installMechanicsController} from "./controller";
import {installMechanicsElementalSources} from "./elemental-sources";
import {installMechanicsHitParameterSources} from "./hit-parameter-sources";
import {installMechanicsReactionSources} from "./reaction-sources";
import {installMechanicsView} from "./view";
import {installMechanicsReports} from "./reports";

export function createMechanicsEditor(ports: MechanicsEditorPorts): MechanicsEditor {
  const editor = {caps: null, shown: false, words: {}} as MechanicsEditor;
  installMechanicsVocabulary(editor, ports);
  installMechanicsSchemaForm(editor, ports);
  installMechanicsController(editor, ports);
  installMechanicsElementalSources(editor, ports);
  installMechanicsHitParameterSources(editor, ports);
  installMechanicsReactionSources(editor, ports);
  installMechanicsView(editor, ports);
  installMechanicsReports(editor, ports);
  return editor;
}
