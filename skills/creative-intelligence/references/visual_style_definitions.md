# Visual-style definitions

Used by `categorize_creative.py` to tag every unique image (by `image_hash`) with one dominant style. Tags are decoration on the actual image, not a substitute — the analysis layer can also reason directly about the cached image file.

Pick exactly one. When two apply, pick the one a strategist would lead with when describing the ad. A storefront with a person standing in front of it is `real_person` if the person is the focal point; `storefront` if the building is.

---

## real_person

A person (or people) is the focal point of the image. Includes the owner, employees, customers, models — anyone whose face, posture, or gesture carries the ad. Distinguishes from `lifestyle` by foregrounding the human; lifestyle uses people as scene-setting elements rather than the subject.

Examples: a brewer holding a pint glass, a baker behind the counter, a salon stylist mid-cut, a yoga instructor leading a class.

## product_shot

A close-up or detail-focused shot of the business's product or output. Food on a plate, a coffee in a cup, hands kneading dough, finished dessert, beer in a glass, clothing on a rack. The product is the subject — minimal environmental context.

Examples: overhead shot of a charcuterie board, three-quarter view of a latte, close-up of a freshly-iced pastry.

## lifestyle

A scene that conveys the experience or atmosphere of the business. Wider framing than `product_shot`, less person-focused than `real_person`. Often includes people but as scene elements (a busy dining room, a coffee shop with regulars, a yoga studio mid-class). The vibe is the subject.

Examples: a packed dining room at golden hour, a brewery taproom with regulars, a sunny morning at a coffee shop.

## storefront

The building exterior or signage is the focal point. Architectural lens — the physical place itself, often with the business name visible. Distinguishes from `lifestyle` by being explicitly about the location rather than the experience inside it.

Examples: facade of a corner bakery, illuminated storefront sign at dusk, exterior with people walking past.

## graphic

A designed graphic — illustration, icon, logo composition, typography arrangement, color blocks, infographic. Not a photograph of a real subject. Distinguishes from `text_heavy` by being design-led; text_heavy is a photo with text overlay dominating the frame.

Examples: a stylized illustration of a brewery, a logo-and-typography card, a chart or diagram, an icon-set composition.

## text_heavy

A photo (any subject) where overlaid text dominates the visual hierarchy — the text is what your eye lands on first, even though there's a real image underneath. Common for stat-led creative ("500+ funded", "$50M+ raised") and for direct-CTA cards.

Examples: a brewery photo with "500+ FUNDED" overlaid in large type, a storefront with "PREQUALIFY TODAY" headline, a person photo with stat callouts.
