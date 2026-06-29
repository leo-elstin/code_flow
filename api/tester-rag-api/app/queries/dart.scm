;; Captures Widgets (Crucial for Flutter)
(class_declaration
  name: (identifier) @widget.name
  (superclass (type_identifier) @super (#match? @super "StatelessWidget|StatefulWidget"))) @widget.definition

;; Captures Methods & Functions (Business Logic)
(method_signature) @method.definition
(function_signature) @function.definition