# Quicken Rental Property Data Entry Automation

Implementation decisions and validation findings are recorded in [PLAN.md](PLAN.md).

## Overview

This project is intended to reduce the amount of manual data entry required for rental property bookkeeping in Quicken.

Instead of entering transactions one at a time, a user will be able to place source documents or screenshots of those documents into a designated workflow. The system will parse the documents, extract the information needed to create Quicken transactions, and present the results for manual review. After approval, the client application will handle feeding the reviewed transaction data into Quicken.

The system should make uncertain or failed extractions visible rather than silently guessing, so the user can correct anything that could not be determined reliably.

## Architecture

The program will use a client-server architecture.

The **web interface** is the primary interface for uploading documents, reviewing and editing proposed transactions, and approving batches. It will work on desktop browsers and phones, with one shared username/password account initially.

The **Windows client** runs on the machine on which Quicken is installed. It will upload documents from a configured folder, maintain a permanent archive, and handle the local interaction required to move approved transactions into Quicken. Entry can be started from a button in the client or from the web interface when the client is connected.

The **server** will run on a Linux server. It will handle document-processing work and other application logic that does not need to run on the Quicken machine.

The exact boundary between client and server can evolve during implementation.

## Document Parsing

The first implementation will use the vision-capable `qwen3.8-27b@q4_k_m` model served by LM Studio at `http://localhost:1234`, with no API key. The model protocol, host, port, model name, and optional API key will be configurable.

The model interface should remain replaceable so that document parsing can instead use a model accessed through OpenRouter or through a Codex CLI proxy-hosted model without requiring the rest of the application to be redesigned.

The parsing layer should extract structured transaction data while preserving uncertainty where the source document does not provide enough information.

## Transaction Data

The most important transaction properties to recover are:

- Payee
- Category
- Tag
- Amount, including whether it is positive or negative
- Payment date
- Associated rental property
- Destination Quicken account, defaulting to the payment year with an override during review

Inspection of the existing export shows property-and-year accounts, separate R&K Properties accounts, and tags used for both property labels and expense descriptions. Account, category, and tag will remain separate fields using the existing names.

The system should follow the structure already used in the Quicken file rather than imposing a new bookkeeping scheme.

## Review and Quicken Integration

Parsed transactions will go through a manual review step before they are added to Quicken. The purpose of the review is to let the user quickly confirm correct entries, fix uncertain fields, and identify documents that could not be parsed adequately.

Every approved transaction must have a payment/transaction date. Review will support bulk assignment, batch approval, and removal/restoration of proposed transactions. Splitting a purchase across properties or categories is outside the first version.

Credit card purchases use their purchase dates; refunds become positive transactions. Card payments, fees, interest, and visibly crossed-out items are ignored. Card properties remain unset until manual review. Ignore other handwriting, except that handwritten tax payment confirmations and dates determine which tax stubs are paid. Unpaid tax stubs are ignored.

Keep source documents indefinitely on the server and in the Windows archive, including documents uploaded through the browser. Preserve originals and use smaller copies for model input and previews.

The eventual workflow should minimize the amount of interaction required after review. Ideally, once a batch has been approved, the Windows client will automate the remaining Quicken import or entry process rather than requiring the user to manually move data between applications.

The precise Quicken integration method should be chosen during implementation based on what proves reliable with the existing Quicken installation and data file.

## Initial Goal

The first version does not need to solve every kind of rental-property document or every possible Quicken workflow. Its goal is to establish a reliable path from a batch of documents to reviewed, structured transactions that can be entered into the existing Quicken data file with substantially less manual work.
