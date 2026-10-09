// bippy's index imports "react" only for its useFiber hook, which the picker never uses.
// Aliasing to an empty module keeps React out of the content-script bundle.
export {};
