// bippy's index imports "react" only for its useFiber hook, which the picker never uses.
// Aliasing to this stub keeps React out of the content-script bundle.
export const useEffect = undefined;
export const useReducer = undefined;
export const useRef = undefined;
