---
timeout: 180
---

# vision/multimodal — test

## Setup

Requires a running agent with the `vision` capability registered and a valid
vision provider configured. The test checks filesystem artifacts and tool
responses.

## Steps

1. `bash({command: "python3 -c \"from PIL import Image; img=Image.new('RGB',(100,100),'red'); img.save('_test_vision.png')\""})` — create a test image.
   If PIL unavailable: `bash({command: "printf '\\x89PNG\\r\\n\\x1a\\n' > _test_vision.png"})` — minimal PNG header.
2. `vision({image_path: "_test_vision.png", question: "What color is this image?"})` — basic call.
3. `vision({image_path: "_test_nonexistent.png"})` — missing file.
4. `vision({})` — missing image_path.
5. `bash({command: "rm -f _test_vision.png"})` — cleanup.

## Pass criteria

- **Step 2**: Returns `{status: "ok", analysis: "<non-empty string>"}`.
  Analysis should mention "red" if the image is a solid red square.
- **Step 3**: Returns `{status: "error"}` with message containing `"not found"`.
- **Step 4**: Returns `{status: "error"}` with message containing `"Provide image_path"`.
- **Step 5**: Test image removed.
- **INCONCLUSIVE**: If no vision provider is configured (capability skipped
  during setup), the `vision` tool will not exist. Check
  `logs/events.jsonl` for `capability_skipped` event.

## Output template

```
### vision/multimodal
- [ ] Step 2 — analysis returned for valid image
- [ ] Step 3 — error for missing file
- [ ] Step 4 — error for missing parameter
- [ ] Step 5 — cleanup done
```
