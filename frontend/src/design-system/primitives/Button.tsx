import {Button as AriaButton, type ButtonProps} from "react-aria-components";
import type {Ref} from "react";

type Props = ButtonProps & {tone?: "default" | "primary"; ref?: Ref<HTMLButtonElement>};

export function Button({tone = "default", className, ref, ...props}: Props) {
  return <AriaButton {...props} ref={ref} className={values => {
    const supplied = typeof className === "function" ? className(values) : className;
    return ["gf-button", tone === "primary" ? "gf-button--primary" : "", supplied].filter(Boolean).join(" ");
  }} />;
}
