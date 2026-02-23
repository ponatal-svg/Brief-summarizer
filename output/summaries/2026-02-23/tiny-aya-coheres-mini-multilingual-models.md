# Tiny Aya - Cohere's Mini Multilingual Models

**Channel:** Sam Witteveen | **Category:** AI | **Duration:** 11.0m 46.0s | **Language:** en

**Source:** [https://www.youtube.com/watch?v=8i0zxyHKbfk](https://www.youtube.com/watch?v=8i0zxyHKbfk)

---

## The Hook
The video addresses the critical challenge of finding effective AI models for non-English and low-resource languages, as most large language models primarily cover common European languages well. Cohere's new "tiny" multilingual models aim to bridge this gap, offering accessible, general-purpose multilingual capabilities.

## Key Findings
*   Many large language models struggle with low-resource languages due to insufficient training data on the internet (e.g., inactive Wikipedias) and inefficient tokenizers that often tokenize languages character by character, hindering learning. [t=30s]
*   Cohere has released a suite of "tiny" multilingual models, each approximately 3.3 billion parameters. This includes a base model pre-trained on 70+ languages, notably encompassing data from many low-resource languages, providing a foundation for diverse multilingual tasks. [t=279s]
*   Beyond the base model, Cohere offers four post-trained models: "tiny global" for general multilingual support, and three specialized regional models – "tiny earth" (covering West Asia, Africa, and European languages), "tiny fire" (focused on South Asian languages like Hindi, Bengali, Tamil, and Nepali), and "tiny water" (for Asia-Pacific languages such as Tagalog, Bahasa, Vietnamese, Thai, Chinese, Karen, and Burmese, plus mixes of West Asia and European languages). [t=341s]
*   Cohere developed its own tokenizer for these models, demonstrating improved efficiency over the Gemma 3 and Quen 3 tokenizers for certain languages. The specialized "tiny" models are mergers of region-specific SFT (Supervised Fine-Tuning) models, for instance, the "tiny water" model merges Europe, West Asia, and Asia-Pacific SFT models. [t=495s]
*   These 3B size models are small enough to run on a phone, making them suitable for mobile applications in languages that larger models do not adequately support. The presenter provides a Colab notebook for users to test the models for specific languages. [t=586s]

## The So What?
According to the presenter, for developers and individuals aiming to build mobile apps or other assistants for countries and languages underserved by mainstream large language models, Cohere's Tiny Aya models present a valuable and accessible solution. This release, alongside advancements like Translate Gemma and improvements in Quen 3.5 models, highlights an ongoing industry focus on enhancing multilingual capabilities, particularly for low-resource languages.
